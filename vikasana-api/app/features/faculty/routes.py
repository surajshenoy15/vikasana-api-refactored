from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, desc, delete
from sqlalchemy.orm import selectinload
import csv
import io

from app.core.database import get_db
from app.core.dependencies import get_current_admin, get_current_faculty

from app.features.auth.models import Admin
from app.features.faculty.models import Faculty, FacultyActivationSession
from app.features.students.models import Student
from app.features.organization.models import College, CollegeAlias

from app.features.activities.models import ActivitySession, ActivitySessionStatus

from app.features.faculty.schemas.faculty import (
    FacultyCreateResponse,
    FacultyResponse,
    ActivateFacultyResponse,
    FacultyCreateRequest,
    FacultyUpdateRequest,
)

from app.features.faculty.schemas.faculty_import import FacultyImportResponse, FailedRow

from app.features.faculty.schemas.faculty_activation import (
    ActivationValidateResponse,
    SendOtpRequest,
    VerifyOtpRequest,
    VerifyOtpResponse,
    SetPasswordRequest,
    SetPasswordResponse,
)

from app.features.faculty.service import (
    create_faculty,
    validate_activation_token_and_create_session,
    send_activation_otp,
    verify_activation_otp,
    set_password_after_otp,
    activate_faculty,
    update_faculty,
)

from app.features.faculty.role_policy import normalize_faculty_role

from pydantic import BaseModel

router = APIRouter(prefix="/faculty", tags=["Faculty"])


# =========================================================
# ADMIN ONLY: CREATE / LIST / DELETE FACULTY
# =========================================================

@router.post("", response_model=FacultyCreateResponse, summary="Create Faculty (Admin only)")
async def add_faculty(
    full_name: str = Form(...),
    college: str = Form(...),
    email: str = Form(...),
    role: str = Form("faculty"),
    department_id: int | None = Form(None),
    image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    payload = FacultyCreateRequest(
        full_name=full_name,
        college=college,
        email=email,
        role=role,
        department_id=department_id,
    )

    image_bytes = None
    if image:
        image_bytes = await image.read()

    faculty, email_sent = await create_faculty(
        payload=payload,
        db=db,
        image_bytes=image_bytes,
        image_content_type=image.content_type if image else None,
        image_filename=image.filename if image else None,
        created_by_admin_id=admin.id,
    )

    message = (
        "Faculty created and activation email sent."
        if email_sent
        else "Faculty created, but activation email could not be sent (email not configured)."
    )

    return {
        "faculty": FacultyResponse.model_validate(faculty),
        "activation_email_sent": email_sent,
        "message": message,
    }


@router.post("/import-csv", response_model=FacultyImportResponse, summary="Import Faculty via CSV (Admin only)")
async def import_faculty_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except Exception:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header row")

    required = {"full_name", "email", "college", "role"}
    headers = {h.strip() for h in reader.fieldnames if h}
    missing = required - headers
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(sorted(missing))}")

    created_faculty: list[FacultyResponse] = []
    failed_rows: list[FailedRow] = []
    created_count = 0
    email_sent_count = 0
    row_number = 1

    for row in reader:
        row_number += 1
        try:
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip().lower()
            college = (row.get("college") or "").strip()
            role = (row.get("role") or "faculty").strip() or "faculty"

            raw_department_id = (row.get("department_id") or "").strip()
            department_id: int | None = None

            if raw_department_id:
                try:
                    department_id = int(raw_department_id)
                except ValueError:
                    raise ValueError("department_id must be a positive integer")

                if department_id <= 0:
                    raise ValueError("department_id must be a positive integer")

            if not full_name:
                raise ValueError("full_name is required")
            if not email or "@" not in email:
                raise ValueError("valid email is required")
            if not college:
                raise ValueError("college is required")

            existing = await db.execute(select(Faculty).where(Faculty.email == email))
            if existing.scalar_one_or_none():
                raise ValueError("email already exists")

            payload = FacultyCreateRequest(
                full_name=full_name,
                college=college,
                email=email,
                role=role,
                department_id=department_id,
            )

            faculty, email_sent = await create_faculty(
                payload=payload,
                db=db,
                image_bytes=None,
                image_content_type=None,
                image_filename=None,
                created_by_admin_id=admin.id,
            )

            created_faculty.append(FacultyResponse.model_validate(faculty))
            created_count += 1
            if email_sent:
                email_sent_count += 1

        except Exception as e:
            failed_rows.append(FailedRow(row_number=row_number, error=str(e)))

    return FacultyImportResponse(
        created_count=created_count,
        failed_count=len(failed_rows),
        activation_email_sent_count=email_sent_count,
        failed_rows=failed_rows,
        created_faculty=created_faculty,
    )


@router.get("", response_model=list[FacultyResponse], summary="List faculty (Admin only)")
async def list_faculty(
    college_id: int | None = Query(
        None,
        ge=1,
        description="Optional canonical college ID. Includes active aliases.",
    ),
    role: str | None = Query(
        None,
        description="Optional faculty role filter.",
    ),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    stmt = select(Faculty)

    # ---------------------------------------------------------
    # Canonical college scope.
    #
    # When college_id is supplied, match the canonical college
    # name plus all active historical aliases.
    #
    # Existing Faculty.college values remain untouched.
    # ---------------------------------------------------------

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

        accepted_names = [
            canonical_college.name,
            *aliases,
        ]

        normalized_names = [
            name.strip().lower()
            for name in accepted_names
            if name and name.strip()
        ]

        stmt = stmt.where(
            func.lower(
                func.trim(Faculty.college)
            ).in_(normalized_names)
        )

    # ---------------------------------------------------------
    # Optional canonical role filter.
    # ---------------------------------------------------------

    if role is not None and role.strip():
        normalized_role = normalize_faculty_role(
            role
        )

        stmt = stmt.where(
            func.lower(
                func.trim(Faculty.role)
            ) == normalized_role
        )

    stmt = stmt.order_by(
        Faculty.created_at.desc()
    )

    result = await db.execute(stmt)

    items = result.scalars().all()

    return [
        FacultyResponse.model_validate(item)
        for item in items
    ]


@router.patch(
    "/{faculty_id}",
    response_model=FacultyResponse,
    summary="Update faculty member (Admin only)",
)
async def patch_faculty(
    faculty_id: int,
    body: FacultyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    faculty = await update_faculty(
        faculty_id=faculty_id,
        payload=body,
        db=db,
    )
    return FacultyResponse.model_validate(faculty)


@router.delete(
    "/{faculty_id}",
    summary="Deactivate faculty member (Admin only)",
)
async def delete_faculty(
    faculty_id: int,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """
    Historical Faculty records must never be physically deleted.

    This legacy DELETE endpoint is intentionally retained for frontend
    backward compatibility, but its behavior is now non-destructive.
    """

    result = await db.execute(
        select(Faculty).where(
            Faculty.id == faculty_id
        )
    )

    faculty = result.scalar_one_or_none()

    if not faculty:
        raise HTTPException(
            status_code=404,
            detail="Faculty not found",
        )

    if not faculty.is_active:
        return {
            "detail": f"Faculty {faculty_id} is already inactive",
            "faculty_id": faculty_id,
            "is_active": False,
        }

    faculty.is_active = False

    try:
        await db.commit()
        await db.refresh(faculty)
    except Exception:
        await db.rollback()
        raise

    return {
        "detail": f"Faculty {faculty_id} deactivated",
        "faculty_id": faculty.id,
        "is_active": faculty.is_active,
    }


# =========================================================
# FACULTY APP: DASHBOARD STATS
# GET /api/faculty/dashboard/stats
# =========================================================

@router.get("/dashboard/stats", summary="Faculty dashboard stats (Faculty auth)")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    students = await db.scalar(
        select(func.count())
        .select_from(Student)
        .where(Student.college == current_faculty.college)
    )

    return {
        "students": int(students or 0),
        "verified": 0,
        "pending": 0,
        "rejected": 0,
    }


# =========================================================
# FACULTY APP: LIST ACTIVITY SESSIONS
# =========================================================

@router.get("/activity-sessions", summary="List activity sessions (Faculty auth)")
async def list_activity_sessions(
    q: str | None = Query(None, description="Search by student name/usn/activity"),
    status: str | None = Query(None, description="DRAFT/SUBMITTED/APPROVED/REJECTED/FLAGGED/EXPIRED"),
    limit: int | None = Query(None, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    stmt = (
        select(ActivitySession)
        .options(selectinload(ActivitySession.student))
        .where(ActivitySession.student.has(Student.college == current_faculty.college))
        .order_by(desc(ActivitySession.created_at))
    )

    if q:
        qq = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            ActivitySession.student.has(
                or_(
                    func.lower(Student.name).like(qq),
                    func.lower(Student.usn).like(qq),
                )
            )
        )

    if status:
        s = status.strip().upper()
        if s in ActivitySessionStatus.__members__:
            stmt = stmt.where(ActivitySession.status == ActivitySessionStatus[s])

    if limit:
        stmt = stmt.limit(limit)

    if offset:
        stmt = stmt.offset(offset)

    sessions = (await db.execute(stmt)).scalars().all()

    activities = []
    for sess in sessions:
        stu = sess.student
        activities.append(
            {
                "id": sess.id,
                "title": sess.activity_name,
                "student_name": stu.name if stu else "—",
                "usn": stu.usn if stu else "—",
                "category": None,
                "description": sess.description,
                "status": (
                    sess.status.value
                    if hasattr(sess.status, "value")
                    else str(sess.status)
                ).lower(),
                "submitted_at": sess.submitted_at or sess.created_at,
            }
        )

    return {"activities": activities, "count": len(activities)}


# =========================================================
# FACULTY APP: UPDATE STATUS
# =========================================================

@router.patch("/activity-sessions/{session_id}/status", summary="Update activity session status (Faculty auth)")
async def update_activity_session_status(
    session_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    raw = (body.get("status") or "").strip().upper()

    if raw in ("APPROVED", "REJECTED", "SUBMITTED", "FLAGGED", "DRAFT", "EXPIRED"):
        new_status = ActivitySessionStatus[raw]
    elif raw.lower() in ("approved", "rejected"):
        new_status = ActivitySessionStatus.APPROVED if raw.lower() == "approved" else ActivitySessionStatus.REJECTED
    else:
        raise HTTPException(status_code=400, detail="status must be approved or rejected")

    q = await db.execute(
        select(ActivitySession)
        .options(selectinload(ActivitySession.student))
        .where(ActivitySession.id == session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activity session not found")

    if not sess.student or (sess.student.college or "") != (current_faculty.college or ""):
        raise HTTPException(status_code=403, detail="Not allowed")

    sess.status = new_status
    await db.commit()
    await db.refresh(sess)

    return {"detail": "Status updated", "id": sess.id, "status": sess.status.value.lower()}


# =========================================================
# ACTIVATION FLOW
# =========================================================

@router.get("/activation/validate", response_model=ActivationValidateResponse, summary="Validate activation token and create activation session")
async def activation_validate(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    session_id, email_masked, expires_at = await validate_activation_token_and_create_session(token, db)
    return {"activation_session_id": session_id, "email_masked": email_masked, "expires_at": expires_at}


@router.post("/activation/send-otp", summary="Send OTP to faculty email")
async def activation_send_otp(
    body: SendOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    await send_activation_otp(body.activation_session_id, db)
    return {"detail": "OTP sent successfully"}


@router.post("/activation/verify-otp", response_model=VerifyOtpResponse, summary="Verify OTP and return set password token")
async def activation_verify_otp(
    body: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    set_password_token = await verify_activation_otp(body.activation_session_id, body.otp, db)
    return {"set_password_token": set_password_token}


@router.post("/activation/set-password", response_model=SetPasswordResponse, summary="Set password after OTP verification")
async def activation_set_password(
    body: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    await set_password_after_otp(body.set_password_token, body.new_password, db)
    return {"detail": "Password set successfully. Account activated."}


@router.get("/activate", response_model=ActivateFacultyResponse, summary="(OLD) Activate faculty account via email token (no OTP)")
async def activate(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    await activate_faculty(token, db)
    return {"detail": "Account activated successfully."}


class VerifyQrRequest(BaseModel):
    qr: str


@router.post("/verify-qr", summary="Verify QR (Faculty auth)")
async def verify_qr(
    body: VerifyQrRequest,
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    qr = (body.qr or "").strip()
    if not qr:
        raise HTTPException(status_code=400, detail="qr is required")

    return {
        "ok": True,
        "detail": "QR received",
        "qr": qr,
        "faculty_id": current_faculty.id,
        "faculty_college": current_faculty.college,
    }