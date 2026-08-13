# app/routes/students.py

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, cast, String, delete
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import get_current_faculty, get_current_admin, get_current_student
from app.features.faculty.models import Faculty
from app.features.faculty.role_policy import (
    ROLE_FACULTY,
    ROLE_HOD,
    normalize_faculty_role,
)
from app.features.auth.models import Admin
from app.features.students.models import Student, StudentType
from app.features.events.models import Event, EventSubmission
from app.features.certificates.models import Certificate
import os
from datetime import timedelta
from app.core.minio_client import get_minio
# ✅ FIX: Required for /students/me visible points calculation
from app.features.activities.models import ActivitySession, ActivityType
from app.features.face.models import StudentFaceEmbedding, StudentFaceEnrollmentImage
from app.features.students.service import (
    create_student,
    create_students_from_csv,
    record_student_assignment_history,
    validate_student_academic_assignment,
)
from app.features.students.points_service import (
    get_student_point_adjustments,
    create_student_point_adjustment,
    update_student_point_adjustment,
    delete_student_point_adjustment,
)

from pydantic import BaseModel, EmailStr
from typing import Optional

from app.features.students.schemas.student import (
    StudentCreate,
    StudentOut,
    BulkUploadResult,
    StudentPointAdjustmentCreate,
    StudentPointAdjustmentUpdate,
    StudentPointAdjustmentOut,
    StudentPointAdjustmentListOut,
    StudentPointAdjustmentWriteResponse,
)

from app.features.organization.models import College, CollegeAlias

from app.features.organization.service import (
    get_organization_settings,
    require_department_architecture_enabled,
)
from app.features.students.schemas.assignment import StudentAssignmentUpdateRequest
from app.features.students.service import apply_student_assignment_update

from app.features.students.schemas.assignment import StudentBulkAssignmentRequest
from app.features.students.schemas.assignment import StudentBulkAssignmentResponse
from app.features.students.service import apply_bulk_student_assignment_update


def _require_student_creation_faculty_role(
    faculty: Faculty,
) -> str:
    """
    Student creation through Faculty-authenticated routes is allowed
    only for HOD and Faculty/Mentor accounts.

    Admin uses the separate Admin student routes.
    College Coordinator and Faculty Coordinator must not create
    students through these endpoints.
    """
    try:
        role = normalize_faculty_role(
            getattr(faculty, "role", "")
        )
    except ValueError as error:
        raise HTTPException(
            status_code=403,
            detail="This Faculty account cannot create students",
        ) from error

    if role not in {
        ROLE_FACULTY,
        ROLE_HOD,
    }:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only HOD or Faculty/Mentor accounts "
                "can create students"
            ),
        )

    return role


async def _prepare_manual_student_create_payload(
    *,
    db: AsyncSession,
    payload: StudentCreate,
    faculty: Faculty,
    role: str,
) -> StudentCreate:
    """
    Derive hierarchy-sensitive student assignment fields from the
    authenticated Faculty account.

    Creator provenance remains handled separately through faculty_id /
    changed_by_faculty_id when create_student() is called.
    """

    faculty_department_id = getattr(
        faculty,
        "department_id",
        None,
    )

    # ---------------------------------------------------------
    # Faculty/Mentor
    # ---------------------------------------------------------
    if role == ROLE_FACULTY:
        # Legacy Faculty accounts may not yet have department_id.
        # Preserve that behavior: created_by_faculty_id remains the
        # legacy mentor link and assigned_faculty_id stays unset.
        if faculty_department_id is None:
            if payload.assigned_faculty_id not in (
                None,
                faculty.id,
            ):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Faculty/Mentor cannot assign a student "
                        "to another Faculty/Mentor"
                    ),
                )

            return payload.model_copy(
                update={
                    "assigned_faculty_id": None,
                }
            )

        if (
            payload.department_id is not None
            and payload.department_id != faculty_department_id
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Faculty/Mentor can create students only "
                    "inside their own department"
                ),
            )

        if (
            payload.assigned_faculty_id is not None
            and payload.assigned_faculty_id != faculty.id
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Faculty/Mentor cannot assign a student "
                    "to another Faculty/Mentor"
                ),
            )

        return payload.model_copy(
            update={
                "department_id": faculty_department_id,
                "assigned_faculty_id": faculty.id,
            }
        )

    # ---------------------------------------------------------
    # HOD
    # ---------------------------------------------------------
    if role == ROLE_HOD:
        if faculty_department_id is None:
            raise HTTPException(
                status_code=409,
                detail="HOD department is not assigned",
            )

        if (
            payload.department_id is not None
            and payload.department_id != faculty_department_id
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "HOD can create students only inside "
                    "their own department"
                ),
            )

        assigned_faculty_id = payload.assigned_faculty_id

        if assigned_faculty_id is not None:
            result = await db.execute(
                select(Faculty).where(
                    Faculty.id == assigned_faculty_id,
                    Faculty.college == faculty.college,
                    Faculty.department_id == faculty_department_id,
                    Faculty.parent_faculty_id == faculty.id,
                    Faculty.role == ROLE_FACULTY,
                    Faculty.is_active.is_(True),
                )
            )

            assigned_faculty = result.scalar_one_or_none()

            if assigned_faculty is None:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Selected Faculty/Mentor must be active, "
                        "belong to the HOD's department, and be "
                        "directly under this HOD"
                    ),
                )

        return payload.model_copy(
            update={
                "department_id": faculty_department_id,
                "assigned_faculty_id": assigned_faculty_id,
            }
        )

    raise HTTPException(
        status_code=403,
        detail="This Faculty account cannot create students",
    )


# ─────────────────────────────────────────────────────────────
# PATCH SCHEMA
# ─────────────────────────────────────────────────────────────
class StudentUpdate(BaseModel):
    college: Optional[str] = None
    name: Optional[str] = None
    usn: Optional[str] = None
    branch: Optional[str] = None
    email: Optional[EmailStr] = None
    student_type: Optional[str] = None
    passout_year: Optional[int] = None
    admitted_year: Optional[int] = None

    # Optional department-wise academic assignments.
    department_id: Optional[int] = None
    batch_id: Optional[int] = None
    current_year: Optional[int] = None
    assigned_faculty_id: Optional[int] = None


class StudentPointsUpdate(BaseModel):
    total_points_earned: int


def _normalize_student_type(v: str | None) -> str | None:
    if v is None:
        return None

    v = str(v).strip().upper()

    if v not in (StudentType.REGULAR.value, StudentType.DIPLOMA.value):
        raise HTTPException(
            status_code=422,
            detail="student_type must be REGULAR or DIPLOMA",
        )

    return v


def _student_out(
    s: Student,
    *,
    activities_count: int = 0,
    certificates_count: int = 0,
) -> StudentOut:
    return StudentOut(
        id=s.id,
        name=s.name,
        usn=s.usn,
        branch=s.branch,
        email=s.email,
        student_type=str(s.student_type),
        passout_year=s.passout_year,
        admitted_year=s.admitted_year,
        college=s.college,
        faculty_mentor_name=(
            s.created_by_faculty.full_name if s.created_by_faculty else None
        ),
        activities_count=int(activities_count or 0),
        certificates_count=int(certificates_count or 0),
        total_points_earned=int(s.total_points_earned or 0),

        # ✅ Soft delete status
        is_active=bool(getattr(s, "is_active", True)),

        department_id=s.department_id,
        batch_id=s.batch_id,
        current_year=s.current_year,
        assigned_faculty_id=s.assigned_faculty_id,
    )

def _point_item_out(item) -> StudentPointAdjustmentOut:
    return StudentPointAdjustmentOut(
        id=item.id,
        activity_name=item.activity_name or "Manual Points",
        category=item.category,
        points=int(item.delta_points or 0),
        date=item.activity_date,
        status=item.status or "approved",
        remarks=item.remarks or item.reason,
        created_at=item.created_at,
    )

def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _face_bucket() -> str:
    if _storage_provider() == "aws":
        return (
            _env("AWS_S3_BUCKET_FACE")
            or _env("MINIO_FACE_BUCKET")
            or "face-verification"
        )

    return _env("MINIO_FACE_BUCKET", "face-verification")


def create_face_enrollment_signed_url(image_key: str) -> str | None:
    if not image_key:
        return None

    try:
        client = get_minio()

        return client.presigned_get_object(
            bucket_name=_face_bucket(),
            object_name=image_key,
            expires=timedelta(minutes=15),
        )

    except Exception as e:
        print(f"⚠️ Failed to create signed URL for face image {image_key}: {e}")
        return None
# ─────────────────────────────────────────────────────────────
# FACULTY ROUTES
# ─────────────────────────────────────────────────────────────
faculty_router = APIRouter(prefix="/faculty/students", tags=["Faculty - Students"])


@faculty_router.get("", response_model=list[StudentOut])
async def list_students(
    q: str | None = Query(None, description="Optional search. Matches name/usn/branch/email."),
    student_type: str | None = Query(None, description="Optional filter: REGULAR or DIPLOMA"),
    branch: str | None = Query(None, description="Optional filter by branch (exact match)."),
    passout_year: int | None = Query(None, description="Optional filter by passout year."),
    admitted_year: int | None = Query(None, description="Optional filter by admitted year."),
    limit: int | None = Query(None, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    activities_sq = (
        select(
            EventSubmission.student_id.label("student_id"),
            func.count(EventSubmission.id).label("activities_count"),
        )
        .group_by(EventSubmission.student_id)
        .subquery()
    )

    certs_sq = (
        select(
            Certificate.student_id.label("student_id"),
            func.count(Certificate.id).label("certificates_count"),
        )
        .group_by(Certificate.student_id)
        .subquery()
    )

    stmt = (
        select(
            Student,
            func.coalesce(activities_sq.c.activities_count, 0).label("activities_count"),
            func.coalesce(certs_sq.c.certificates_count, 0).label("certificates_count"),
        )
        .options(selectinload(Student.created_by_faculty))
        .outerjoin(activities_sq, activities_sq.c.student_id == Student.id)
        .outerjoin(certs_sq, certs_sq.c.student_id == Student.id)
        .where(Student.college == current_faculty.college)
    )

    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Student.name.ilike(like),
                Student.usn.ilike(like),
                Student.branch.ilike(like),
                Student.email.ilike(like),
            )
        )

    if student_type and student_type.strip():
        stmt = stmt.where(Student.student_type == student_type.strip().upper())

    if branch and branch.strip():
        stmt = stmt.where(Student.branch == branch.strip())

    if passout_year is not None:
        stmt = stmt.where(Student.passout_year == passout_year)

    if admitted_year is not None:
        stmt = stmt.where(Student.admitted_year == admitted_year)

    stmt = stmt.order_by(Student.id.desc()).limit(limit).offset(offset)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        _student_out(
            s,
            activities_count=int(activities_count or 0),
            certificates_count=int(certificates_count or 0),
        )
        for (s, activities_count, certificates_count) in rows
    ]


@faculty_router.post("", response_model=StudentOut)
async def add_student_manual(
    payload: StudentCreate,
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    role = _require_student_creation_faculty_role(
        current_faculty
    )

    safe_payload = await _prepare_manual_student_create_payload(
        db=db,
        payload=payload,
        faculty=current_faculty,
        role=role,
    )

    try:
        s = await create_student(
            db,
            safe_payload,
            faculty_college=current_faculty.college,
            faculty_id=current_faculty.id,
            changed_by_faculty_id=current_faculty.id,
        )

        return _student_out(s, activities_count=0, certificates_count=0)

    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@faculty_router.post("/bulk-upload", response_model=BulkUploadResult)
async def add_students_bulk(
    file: UploadFile = File(...),
    skip_duplicates: bool = Query(True, description="If true, existing USNs/emails will be skipped"),
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    role = _require_student_creation_faculty_role(
        current_faculty
    )

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv file is allowed")

    data = await file.read()

    total, inserted, skipped, invalid, errors = await create_students_from_csv(
        db=db,
        csv_bytes=data,
        skip_duplicates=skip_duplicates,
        faculty_college=current_faculty.college,
        faculty_id=current_faculty.id,
        changed_by_faculty_id=current_faculty.id,
        hierarchy_role=role,
        hierarchy_department_id=current_faculty.department_id,
        hierarchy_faculty_id=current_faculty.id,
    )

    return BulkUploadResult(
        total_rows=total,
        inserted=inserted,
        skipped_duplicates=skipped,
        invalid_rows=invalid,
        errors=errors,
    )


# ─────────────────────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────────────────────
admin_router = APIRouter(prefix="/admin/students", tags=["Admin - Students"])
@admin_router.post("", response_model=StudentOut)
async def add_student_manual_admin(
    payload: StudentCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    try:
        # Admin can set college from payload.
        # faculty_id is None because admin is creating directly.
        admin_student_college = (
            getattr(payload, "college", None)
            or getattr(payload, "college_name", None)
            or "BNMIT"
        )

        organization_settings = await get_organization_settings(
            db,
            admin_student_college,
        )

        s = await create_student(
            db,
            payload,
            faculty_college=admin_student_college,
            faculty_id=None,
            changed_by_admin_id=current_admin.id,
            require_hierarchy_faculty=(
                organization_settings.department_architecture_enabled
            ),
        )

        return _student_out(s, activities_count=0, certificates_count=0)

    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@admin_router.post("/bulk-upload", response_model=BulkUploadResult)
async def add_students_bulk_admin(
    file: UploadFile = File(...),
    skip_duplicates: bool = Query(True, description="If true, existing USNs/emails will be skipped"),
    college: str = Query("BNMIT", description="Default college for uploaded students"),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv file is allowed")

    data = await file.read()

    total, inserted, skipped, invalid, errors = await create_students_from_csv(
    db=db,
    csv_bytes=data,
    skip_duplicates=skip_duplicates,
    faculty_college=college,
    faculty_id=None,
    changed_by_admin_id=current_admin.id,
    allow_csv_college=True,
    allow_csv_faculty_email=True,
)

    return BulkUploadResult(
        total_rows=total,
        inserted=inserted,
        skipped_duplicates=skipped,
        invalid_rows=invalid,
        errors=errors,
    )

@admin_router.get("", response_model=list[StudentOut])
async def list_students_admin(
    q: str | None = Query(None, description="Optional search. Matches name/usn/branch/email."),
    college: str | None = Query(None, description="Optional legacy filter by college (exact match)."),
    college_id: int | None = Query(
        None,
        ge=1,
        description="Optional canonical college ID. Includes active aliases.",
    ),
    student_type: str | None = Query(None, description="Optional filter: REGULAR or DIPLOMA"),
    branch: str | None = Query(None, description="Optional filter by branch (exact match)."),
    passout_year: int | None = Query(None, description="Optional filter by passout year."),
    admitted_year: int | None = Query(None, description="Optional filter by admitted year."),
    assigned_faculty_id: int | None = Query(
        None,
        ge=1,
        description="Optional filter by assigned Faculty/Mentor ID.",
    ),

    # ✅ NEW: active / inactive filter
    is_active: bool | None = Query(True, description="Optional filter: true=active, false=inactive"),

    limit: int | None = Query(None, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    activities_sq = (
        select(
            EventSubmission.student_id.label("student_id"),
            func.count(EventSubmission.id).label("activities_count"),
        )
        .group_by(EventSubmission.student_id)
        .subquery()
    )

    certs_sq = (
        select(
            Certificate.student_id.label("student_id"),
            func.count(Certificate.id).label("certificates_count"),
        )
        .group_by(Certificate.student_id)
        .subquery()
    )

    stmt = (
        select(
            Student,
            func.coalesce(activities_sq.c.activities_count, 0).label("activities_count"),
            func.coalesce(certs_sq.c.certificates_count, 0).label("certificates_count"),
        )
        .options(selectinload(Student.created_by_faculty))
        .outerjoin(activities_sq, activities_sq.c.student_id == Student.id)
        .outerjoin(certs_sq, certs_sq.c.student_id == Student.id)
    )

    # ✅ NEW: filter active/inactive only when query param is passed
    if is_active is not None:
        stmt = stmt.where(Student.is_active == is_active)

    # Canonical college scope for Admin drill-down.
    #
    # When college_id is supplied, include the canonical college
    # name plus all active historical aliases. Existing Student.college
    # values remain untouched.
    if college_id is not None:
        college_result = await db.execute(
            select(College).where(
                College.id == college_id
            )
        )

        canonical_college = (
            college_result.scalar_one_or_none()
        )

        if canonical_college is None:
            raise HTTPException(
                status_code=404,
                detail="College not found",
            )

        alias_result = await db.execute(
            select(CollegeAlias.alias).where(
                CollegeAlias.college_id == college_id,
                CollegeAlias.is_active.is_(True),
            )
        )

        aliases = alias_result.scalars().all()

        college_scope_names = [
            canonical_college.name,
            *aliases,
        ]

        normalized_scope_names = list(
            {
                str(name).strip().lower()
                for name in college_scope_names
                if name and str(name).strip()
            }
        )

        stmt = stmt.where(
            func.lower(
                func.trim(Student.college)
            ).in_(normalized_scope_names)
        )

    elif college and college.strip():
        # Preserve the existing legacy exact-college behaviour
        # for callers that still use ?college=...
        stmt = stmt.where(
            Student.college == college.strip()
        )

    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Student.name.ilike(like),
                Student.usn.ilike(like),
                Student.branch.ilike(like),
                Student.email.ilike(like),
            )
        )

    if student_type and student_type.strip():
        stmt = stmt.where(Student.student_type == student_type.strip().upper())

    if branch and branch.strip():
        stmt = stmt.where(Student.branch == branch.strip())

    if passout_year is not None:
        stmt = stmt.where(Student.passout_year == passout_year)

    if admitted_year is not None:
        stmt = stmt.where(Student.admitted_year == admitted_year)

    if assigned_faculty_id is not None:
        # Department hierarchy compatibility:
        #
        # New records use assigned_faculty_id explicitly.
        # Legacy records may only have created_by_faculty_id,
        # which historically represented the faculty/mentor.
        #
        # Never let the legacy creator override an explicit
        # hierarchy assignment.
        stmt = stmt.where(
            or_(
                Student.assigned_faculty_id == assigned_faculty_id,
                and_(
                    Student.assigned_faculty_id.is_(None),
                    Student.created_by_faculty_id == assigned_faculty_id,
                ),
            )
        )

    stmt = stmt.order_by(Student.id.desc())

    if limit:
        stmt = stmt.limit(limit)

    if offset:
        stmt = stmt.offset(offset)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        _student_out(
            s,
            activities_count=int(activities_count or 0),
            certificates_count=int(certificates_count or 0),
        )
        for (s, activities_count, certificates_count) in rows
    ]
@admin_router.get("/{student_id}/face-enrollment")
async def get_student_face_enrollment_details(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    student_result = await db.execute(
        select(Student).where(Student.id == student_id)
    )
    student = student_result.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    img_result = await db.execute(
        select(StudentFaceEnrollmentImage)
        .where(StudentFaceEnrollmentImage.student_id == student_id)
        .order_by(
            StudentFaceEnrollmentImage.slot.asc(),
            StudentFaceEnrollmentImage.id.asc(),
        )
    )
    images_db = img_result.scalars().all()

    emb_result = await db.execute(
        select(StudentFaceEmbedding).where(
            StudentFaceEmbedding.student_id == student_id
        )
    )
    embedding = emb_result.scalar_one_or_none()

    images = []

    for idx, img in enumerate(images_db, start=1):
        signed_url = None

        if img.image_key:
            signed_url = create_face_enrollment_signed_url(img.image_key)

        images.append(
            {
                "id": img.id,
                "slot": img.slot or idx,
                "image_url": img.image_url,
                "image_key": img.image_key,
                "signed_url": signed_url,
                "captured_at": img.created_at,
            }
        )

    face_count = len(images)
    required_count = 5

    if face_count >= required_count:
        status = "COMPLETED"
    elif face_count > 0:
        status = "PARTIAL"
    elif embedding and embedding.photo_count:
        status = "EMBEDDING_ONLY"
    else:
        status = "PENDING"

    return {
        "student": {
            "id": student.id,
            "name": student.name,
            "email": student.email,
            "usn": student.usn,
            "branch": student.branch,
            "college": student.college,
        },
        "face_count": face_count,
        "embedding_photo_count": embedding.photo_count if embedding else 0,
        "required_count": required_count,
        "status": status,
        "images": images,
    }

@admin_router.delete("/{student_id}/face-enrollment")
async def reset_student_face_enrollment(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    student_result = await db.execute(
        select(Student).where(Student.id == student_id)
    )
    student = student_result.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    await db.execute(
        delete(StudentFaceEnrollmentImage).where(
            StudentFaceEnrollmentImage.student_id == student_id
        )
    )

    await db.execute(
        delete(StudentFaceEmbedding).where(
            StudentFaceEmbedding.student_id == student_id
        )
    )

    student.face_enrolled = False
    student.face_enrolled_at = None

    await db.commit()

    return {
        "ok": True,
        "student_id": student.id,
        "message": "Face enrollment reset. Student must capture face images again.",
    }
@admin_router.patch("/{student_id}", response_model=StudentOut)
async def update_student_admin(
    student_id: int,
    payload: StudentUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    res = await db.execute(
        select(Student)
        .options(selectinload(Student.created_by_faculty))
        .where(Student.id == student_id)
    )

    s = res.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Student not found")

    data = payload.model_dump(exclude_unset=True)

    if not data:
        raise HTTPException(
            status_code=400,
            detail="No student fields provided for update",
        )

    previous_department_id = s.department_id
    previous_batch_id = s.batch_id
    previous_current_year = s.current_year
    previous_assigned_faculty_id = s.assigned_faculty_id

    if "name" in data and data["name"] is not None:
        data["name"] = str(data["name"]).strip()

    if "college" in data and data["college"] is not None:
        data["college"] = str(data["college"]).strip()

    if "usn" in data and data["usn"] is not None:
        data["usn"] = str(data["usn"]).strip()

    if "branch" in data and data["branch"] is not None:
        data["branch"] = str(data["branch"]).strip()

    if "email" in data and data["email"] is not None:
        data["email"] = str(data["email"]).strip().lower()

    if "student_type" in data:
        data["student_type"] = _normalize_student_type(data.get("student_type"))

    if "usn" in data and data["usn"]:
        dup = await db.execute(
            select(Student.id).where(Student.usn == data["usn"], Student.id != s.id)
        )

        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="USN already exists")

    if "email" in data and data["email"]:
        dup = await db.execute(
            select(Student.id).where(Student.email == data["email"], Student.id != s.id)
        )

        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already exists")

    academic_fields = {
        "department_id",
        "batch_id",
        "current_year",
        "assigned_faculty_id",
    }

    academic_update_requested = bool(
        academic_fields.intersection(data)
    )

    college_update_requested = "college" in data

    if academic_update_requested or college_update_requested:
        target_college = data.get("college", s.college)
        target_department_id = data.get(
            "department_id",
            s.department_id,
        )
        target_batch_id = data.get(
            "batch_id",
            s.batch_id,
        )
        target_current_year = data.get(
            "current_year",
            s.current_year,
        )
        target_assigned_faculty_id = data.get(
            "assigned_faculty_id",
            s.assigned_faculty_id,
        )

        has_academic_assignment = any(
            value is not None
            for value in (
                target_department_id,
                target_batch_id,
                target_current_year,
                target_assigned_faculty_id,
            )
        )

        if has_academic_assignment:
            await validate_student_academic_assignment(
                db=db,
                college=target_college,
                department_id=target_department_id,
                batch_id=target_batch_id,
                current_year=target_current_year,
                assigned_faculty_id=(
                    target_assigned_faculty_id
                ),
            )

    for key, value in data.items():
        # Preserve the existing behaviour for legacy fields:
        # null does not clear name, email, college, USN, etc.
        #
        # Academic assignment fields are nullable, so explicit
        # null clears them while omitted fields remain unchanged.
        if value is None and key not in academic_fields:
            continue

        setattr(s, key, value)

    if academic_update_requested:
        record_student_assignment_history(
            db=db,
            student=s,
            previous_department_id=previous_department_id,
            new_department_id=s.department_id,
            previous_batch_id=previous_batch_id,
            new_batch_id=s.batch_id,
            previous_year=previous_current_year,
            new_year=s.current_year,
            previous_faculty_id=previous_assigned_faculty_id,
            new_faculty_id=s.assigned_faculty_id,
            changed_by_admin_id=getattr(
                current_admin,
                "id",
                None,
            ),
            reason="Admin student academic assignment update",
        )

    await db.commit()
    await db.refresh(s)

    act_res = await db.execute(
        select(func.count(EventSubmission.id)).where(EventSubmission.student_id == s.id)
    )

    activities_count = act_res.scalar() or 0

    cert_res = await db.execute(
        select(func.count(Certificate.id)).where(Certificate.student_id == s.id)
    )

    certificates_count = cert_res.scalar() or 0

    return _student_out(
        s,
        activities_count=int(activities_count),
        certificates_count=int(certificates_count),
    )
@admin_router.patch(
    '/assignment/bulk',
    response_model=StudentBulkAssignmentResponse,
)
async def update_students_assignment_bulk_admin(
    payload: StudentBulkAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    """
    Apply one academic/faculty assignment update to many students.

    Every student must exist. Department architecture must be
    enabled for every involved college. The service validates all
    final states before mutating any student.
    """
    result = await db.execute(
        select(Student)
        .options(
            selectinload(
                Student.created_by_faculty
            )
        )
        .where(
            Student.id.in_(
                payload.student_ids
            )
        )
    )

    found_students = list(
        result.scalars().all()
    )

    students_by_id = {
        student.id: student
        for student in found_students
    }

    missing_student_ids = [
        student_id
        for student_id in payload.student_ids
        if student_id not in students_by_id
    ]

    if missing_student_ids:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Students not found",
                "student_ids": missing_student_ids,
            },
        )

    students = [
        students_by_id[student_id]
        for student_id in payload.student_ids
    ]

    checked_colleges = set()

    for student in students:
        college_key = str(
            student.college or ""
        ).strip().casefold()

        if college_key in checked_colleges:
            continue

        await require_department_architecture_enabled(
            db,
            student.college,
        )

        checked_colleges.add(college_key)

    assignment_data = payload.model_dump(
        exclude_unset=True,
        exclude={"student_ids"},
    )

    await apply_bulk_student_assignment_update(
        db=db,
        students=students,
        assignment_data=assignment_data,
        changed_by_admin_id=getattr(
            current_admin,
            "id",
            None,
        ),
        reason=(
            "Admin bulk student assignment workflow"
        ),
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return StudentBulkAssignmentResponse(
        updated_count=len(students),
        student_ids=[
            student.id
            for student in students
        ],
    )




@admin_router.patch(
    '/{student_id}/assignment',
    response_model=StudentOut,
)
async def update_student_assignment_admin(
    student_id: int,
    payload: StudentAssignmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    """
    Apply an academic or faculty assignment update to one student.

    Department architecture must be enabled for the student's
    college. The assignment service performs validation and adds
    assignment history without committing independently.
    """
    result = await db.execute(
        select(Student)
        .options(
            selectinload(
                Student.created_by_faculty
            )
        )
        .where(Student.id == student_id)
    )

    student = result.scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=404,
            detail="Student not found",
        )

    await require_department_architecture_enabled(
        db,
        student.college,
    )

    assignment_data = payload.model_dump(
        exclude_unset=True,
    )

    await apply_student_assignment_update(
        db=db,
        student=student,
        assignment_data=assignment_data,
        changed_by_admin_id=getattr(
            current_admin,
            "id",
            None,
        ),
        reason=(
            "Admin student assignment workflow"
        ),
    )

    await db.commit()
    await db.refresh(student)

    activity_result = await db.execute(
        select(
            func.count(EventSubmission.id)
        ).where(
            EventSubmission.student_id
            == student.id
        )
    )

    activities_count = (
        activity_result.scalar() or 0
    )

    certificate_result = await db.execute(
        select(
            func.count(Certificate.id)
        ).where(
            Certificate.student_id
            == student.id
        )
    )

    certificates_count = (
        certificate_result.scalar() or 0
    )

    return _student_out(
        student,
        activities_count=int(
            activities_count
        ),
        certificates_count=int(
            certificates_count
        ),
    )



@admin_router.delete("/{student_id}")
async def delete_student_admin(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    s = await db.get(Student, student_id)

    if not s:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        # Delete linked certificates first because certificates.student_id references students.id
        await db.execute(
            delete(Certificate).where(Certificate.student_id == student_id)
        )

        # Delete event submissions linked to student
        await db.execute(
            delete(EventSubmission).where(EventSubmission.student_id == student_id)
        )

        # Delete activity sessions linked to student
        await db.execute(
            delete(ActivitySession).where(ActivitySession.student_id == student_id)
        )

        # Delete face enrollment images
        await db.execute(
            delete(StudentFaceEnrollmentImage).where(
                StudentFaceEnrollmentImage.student_id == student_id
            )
        )

        # Delete face embeddings
        await db.execute(
            delete(StudentFaceEmbedding).where(
                StudentFaceEmbedding.student_id == student_id
            )
        )

        # Finally delete student permanently
        await db.execute(
            delete(Student).where(Student.id == student_id)
        )

        await db.commit()

        return {
            "success": True,
            "message": "Student permanently deleted successfully",
            "student_id": student_id,
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete student permanently: {str(e)}",
        )
@admin_router.patch("/{student_id}/points")
async def update_student_points_admin(
    student_id: int,
    payload: StudentPointsUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    s = await db.get(Student, student_id)

    if not s:
        raise HTTPException(status_code=404, detail="Student not found")

    if payload.total_points_earned < 0:
        raise HTTPException(status_code=400, detail="Points cannot be negative")

    s.total_points_earned = int(payload.total_points_earned)

    await db.commit()
    await db.refresh(s)

    return {
        "id": s.id,
        "total_points_earned": int(s.total_points_earned or 0),
    }


# ─────────────────────────────────────────────────────────────
# ADMIN ACTIVITY POINTS ROUTES
# ─────────────────────────────────────────────────────────────
@admin_router.get("/{student_id}/activity-points", response_model=StudentPointAdjustmentListOut)
async def get_student_activity_points_admin(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    try:
        student, items = await get_student_point_adjustments(db, student_id=student_id)

        return StudentPointAdjustmentListOut(
            total_points=int(student.total_points_earned or 0),
            items=[_point_item_out(x) for x in items],
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.post("/{student_id}/activity-points", response_model=StudentPointAdjustmentWriteResponse)
async def create_student_activity_point_admin(
    student_id: int,
    payload: StudentPointAdjustmentCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    try:
        item, total_points = await create_student_point_adjustment(
            db,
            student_id=student_id,
            activity_name=payload.activity_name,
            category=payload.category,
            points=payload.points,
            date=payload.date,
            status=payload.status,
            remarks=payload.remarks,
            created_by_admin_id=current_admin.id,
        )

        return StudentPointAdjustmentWriteResponse(
            total_points=int(total_points),
            item=_point_item_out(item),
        )

    except ValueError as e:
        msg = str(e)

        if msg == "Student not found":
            raise HTTPException(status_code=404, detail=msg)

        raise HTTPException(status_code=400, detail=msg)


activity_points_admin_router = APIRouter(
    prefix="/admin/activity-points",
    tags=["Admin - Activity Points"],
)


@activity_points_admin_router.put("/{adjustment_id}", response_model=StudentPointAdjustmentWriteResponse)
async def update_student_activity_point_admin(
    adjustment_id: int,
    payload: StudentPointAdjustmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    try:
        item, total_points = await update_student_point_adjustment(
            db,
            adjustment_id=adjustment_id,
            activity_name=payload.activity_name,
            category=payload.category,
            points=payload.points,
            date=payload.date,
            status=payload.status,
            remarks=payload.remarks,
        )

        return StudentPointAdjustmentWriteResponse(
            total_points=int(total_points),
            item=_point_item_out(item),
        )

    except ValueError as e:
        msg = str(e)

        if msg in {"Activity point entry not found", "Student not found"}:
            raise HTTPException(status_code=404, detail=msg)

        raise HTTPException(status_code=400, detail=msg)


@activity_points_admin_router.delete("/{adjustment_id}")
async def delete_student_activity_point_admin(
    adjustment_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    try:
        total_points = await delete_student_point_adjustment(
            db,
            adjustment_id=adjustment_id,
        )

        return {
            "success": True,
            "total_points": int(total_points),
        }

    except ValueError as e:
        msg = str(e)

        if msg in {"Activity point entry not found", "Student not found"}:
            raise HTTPException(status_code=404, detail=msg)

        raise HTTPException(status_code=400, detail=msg)


# ─────────────────────────────────────────────────────────────
# STUDENT ROUTES (PROFILE)
# ─────────────────────────────────────────────────────────────
def _event_visible_conditions():
    conditions = []

    if hasattr(Event, "is_deleted"):
        conditions.append(Event.is_deleted == False)

    if hasattr(Event, "deleted_at"):
        conditions.append(Event.deleted_at.is_(None))

    if hasattr(Event, "is_active"):
        conditions.append(Event.is_active == True)

    return conditions


async def _calculate_visible_student_points(db: AsyncSession, student_id: int) -> int:
    """
    Calculate student points from approved activity sessions.

    Fix:
    - Avoids strict Event.title == ActivitySession.activity_name join.
    - That join can fail if event title and activity name are slightly different.
    - So points were becoming 0 even though approved sessions exist.
    """

    try:
        stmt = (
            select(ActivitySession)
            .where(
                ActivitySession.student_id == student_id,
                func.lower(cast(ActivitySession.status, String)) == "approved",
            )
            .order_by(ActivitySession.id.desc())
        )

        sessions = (await db.execute(stmt)).scalars().all()

        total_points = 0

        for session in sessions:
            points = (
                getattr(session, "points_awarded", None)
                or getattr(session, "awarded_points", None)
                or getattr(session, "points", None)
                or getattr(session, "total_points", None)
                or 0
            )

            total_points += max(int(points or 0), 0)

        return int(total_points)

    except Exception as e:
        print(f"[WARN] visible points calculation failed for student_id={student_id}: {e}")

        # ✅ Safe fallback: do not crash /students/me
        student = await db.get(Student, student_id)

        if student:
            return int(getattr(student, "total_points_earned", 0) or 0)

        return 0

student_router = APIRouter(prefix="/students", tags=["Student - Profile"])


@student_router.get("/me")
async def get_student_me(
    db: AsyncSession = Depends(get_db),
    current_student: Student = Depends(get_current_student),
):
    # ✅ Block deactivated / soft-deleted students
    if getattr(current_student, "is_active", True) is False:
        raise HTTPException(
            status_code=403,
            detail="Student account is inactive. Please contact admin.",
        )

    stored_points = int(getattr(current_student, "total_points_earned", 0) or 0)
    required_points = int(getattr(current_student, "required_total_points", 0) or 0)

    return {
        "id": getattr(current_student, "id", None),
        "name": getattr(current_student, "name", "") or "",
        "email": getattr(current_student, "email", "") or "",
        "college": getattr(current_student, "college", "") or "",
        "usn": getattr(current_student, "usn", "") or "",
        "branch": getattr(current_student, "branch", "") or "",

        # ✅ Send active status to app
        "is_active": bool(getattr(current_student, "is_active", True)),

        "face_enrolled": bool(getattr(current_student, "face_enrolled", False)),
        "face_enrolled_at": getattr(current_student, "face_enrolled_at", None),
        "required_total_points": required_points,

        # ✅ official admin table points
        "total_points_earned": stored_points,
    }