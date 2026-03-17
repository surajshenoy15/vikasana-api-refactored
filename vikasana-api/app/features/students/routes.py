# app/routes/students.py

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import get_current_faculty, get_current_admin, get_current_student
from app.features.faculty.models import Faculty
from app.features.auth.models import Admin
from app.features.students.models import Student, StudentType
from app.features.events.models import EventSubmission
from app.features.certificates.models import Certificate

from app.features.students.service import create_student, create_students_from_csv
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


class StudentPointsUpdate(BaseModel):
    total_points_earned: int


def _normalize_student_type(v: str | None) -> str | None:
    if v is None:
        return None
    v = str(v).strip().upper()
    if v not in (StudentType.REGULAR.value, StudentType.DIPLOMA.value):
        raise HTTPException(status_code=422, detail="student_type must be REGULAR or DIPLOMA")
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
        faculty_mentor_name=(s.created_by_faculty.full_name if s.created_by_faculty else None),
        activities_count=int(activities_count or 0),
        certificates_count=int(certificates_count or 0),
        total_points_earned=int(s.total_points_earned or 0),
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
    limit: int = Query(50, ge=1, le=200),
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
    try:
        s = await create_student(
            db,
            payload,
            faculty_college=current_faculty.college,
            faculty_id=current_faculty.id,
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
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv file is allowed")

    data = await file.read()

    total, inserted, skipped, invalid, errors = await create_students_from_csv(
        db=db,
        csv_bytes=data,
        skip_duplicates=skip_duplicates,
        faculty_college=current_faculty.college,
        faculty_id=current_faculty.id,
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


@admin_router.get("", response_model=list[StudentOut])
async def list_students_admin(
    q: str | None = Query(None, description="Optional search. Matches name/usn/branch/email."),
    college: str | None = Query(None, description="Optional filter by college (exact match)."),
    student_type: str | None = Query(None, description="Optional filter: REGULAR or DIPLOMA"),
    branch: str | None = Query(None, description="Optional filter by branch (exact match)."),
    passout_year: int | None = Query(None, description="Optional filter by passout year."),
    admitted_year: int | None = Query(None, description="Optional filter by admitted year."),
    limit: int = Query(50, ge=1, le=200),
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

    if college and college.strip():
        stmt = stmt.where(Student.college == college.strip())

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

    for k, v in data.items():
        if v is None:
            continue
        setattr(s, k, v)

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


activity_points_admin_router = APIRouter(prefix="/admin/activity-points", tags=["Admin - Activity Points"])


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
student_router = APIRouter(prefix="/students", tags=["Student - Profile"])


@student_router.get("/me")
async def get_student_me(
    current_student: Student = Depends(get_current_student),
):
    return {
        "id": current_student.id,
        "name": current_student.name,
        "email": current_student.email,
        "college": current_student.college,
        "usn": current_student.usn,
        "branch": current_student.branch,
        "face_enrolled": current_student.face_enrolled,
        "face_enrolled_at": current_student.face_enrolled_at,
        "required_total_points": current_student.required_total_points,
        "total_points_earned": current_student.total_points_earned,
    }