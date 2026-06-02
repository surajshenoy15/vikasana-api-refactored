from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case

from app.core.database import get_db
from app.core.dependencies import get_current_admin

from app.features.students.models import Student
from app.features.faculty.models import Faculty
from app.features.events.models import Event, EventSubmission
from app.features.certificates.models import Certificate
from app.features.activities.models import ActivityType

router = APIRouter(prefix="/admin/dashboard", tags=["Admin - Dashboard"])


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _color_for_activity(name: str) -> str:
    n = (name or "").lower()
    if "nss" in n or "volunteer" in n:
        return "emerald"
    if "sports" in n:
        return "amber"
    if "culture" in n or "cultural" in n:
        return "pink"
    return "blue"


def _is_approved_status_col(col):
    return func.lower(col) == "approved"


def _status_to_str(status) -> str:
    if status is None:
        return "—"

    value = getattr(status, "value", status)
    return str(value)


def _safe_event_title(title, event_id=None) -> str:
    t = (title or "").strip()
    if t:
        return t
    if event_id:
        return f"Event #{event_id}"
    return "Unknown Event"


# ─────────────────────────────────────────────────────────────
# 1) STATS
# ─────────────────────────────────────────────────────────────

@router.get("/stats")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    total_students = (
        await db.execute(select(func.count(Student.id)))
    ).scalar() or 0

    try:
        active_students = (
            await db.execute(
                select(func.count(Student.id)).where(Student.is_active == True)
            )
        ).scalar() or 0
    except Exception:
        active_students = total_students

    total_faculty = (
        await db.execute(select(func.count(Faculty.id)))
    ).scalar() or 0

    try:
        pending_faculty = (
            await db.execute(
                select(func.count(Faculty.id)).where(Faculty.is_active == False)
            )
        ).scalar() or 0
    except Exception:
        pending_faculty = 0

    total_submissions = (
        await db.execute(select(func.count(EventSubmission.id)))
    ).scalar() or 0

    approved_submissions = (
        await db.execute(
            select(func.count(EventSubmission.id)).where(
                _is_approved_status_col(EventSubmission.status)
            )
        )
    ).scalar() or 0

    total_certificates = (
        await db.execute(select(func.count(Certificate.id)))
    ).scalar() or 0

    return {
        "totalStudents": int(total_students),
        "activeStudents": int(active_students),
        "totalFaculty": int(total_faculty),
        "pendingFaculty": int(pending_faculty),
        "totalActivities": int(total_submissions),
        "approvedActivities": int(approved_submissions),
        "totalCertificates": int(total_certificates),
        "asOf": None,
    }


# ─────────────────────────────────────────────────────────────
# 2) CATEGORY PROGRESS
# ─────────────────────────────────────────────────────────────

@router.get("/category-progress")
async def category_progress(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    stmt = (
        select(
            ActivityType.name.label("label"),
            func.count(Certificate.id).label("submitted"),
            func.sum(
                case(
                    (_is_approved_status_col(EventSubmission.status), 1),
                    else_=0,
                )
            ).label("approved"),
        )
        .select_from(Certificate)
        .join(ActivityType, ActivityType.id == Certificate.activity_type_id)
        .join(EventSubmission, EventSubmission.id == Certificate.submission_id)
        .group_by(ActivityType.name)
        .order_by(ActivityType.name.asc())
    )

    rows = (await db.execute(stmt)).all()

    return [
        {
            "label": r.label,
            "color": _color_for_activity(r.label),
            "submitted": int(r.submitted or 0),
            "approved": int(r.approved or 0),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────
# 3) STUDENT PROGRESS
# activities = count of EventSubmissions by student
# certificates = count of Certificates by student
# ─────────────────────────────────────────────────────────────

@router.get("/student-progress")
async def student_progress(
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    act_stmt = (
        select(
            Student.id.label("id"),
            Student.name.label("name"),
            func.count(EventSubmission.id).label("activities"),
        )
        .select_from(Student)
        .join(EventSubmission, EventSubmission.student_id == Student.id, isouter=True)
        .group_by(Student.id, Student.name)
        .order_by(func.count(EventSubmission.id).desc())
        .limit(limit)
    )

    act_rows = (await db.execute(act_stmt)).all()
    student_ids = [r.id for r in act_rows]

    cert_map = {}

    if student_ids:
        cert_stmt = (
            select(
                Certificate.student_id,
                func.count(Certificate.id).label("certificate_count"),
            )
            .where(Certificate.student_id.in_(student_ids))
            .group_by(Certificate.student_id)
        )

        cert_rows = (await db.execute(cert_stmt)).all()
        cert_map = {
            sid: int(cnt or 0)
            for sid, cnt in cert_rows
        }

    return [
        {
            "id": r.id,
            "name": r.name,
            "activities": int(r.activities or 0),
            "certificates": cert_map.get(r.id, 0),
        }
        for r in act_rows
    ]


# ─────────────────────────────────────────────────────────────
# 4) RECENT SUBMISSIONS
# Shows real event title instead of hardcoded "Event Submission"
# certificate = if any certificate exists for that submission_id
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# 4) RECENT SUBMISSIONS
# Shows real event title instead of hardcoded "Event Submission"
# certificate = if any certificate exists for that submission_id
# ─────────────────────────────────────────────────────────────

@router.get("/recent-submissions")
async def recent_submissions(
    limit: int = Query(6, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    stmt = (
        select(
            EventSubmission.id.label("submission_id"),
            EventSubmission.event_id.label("event_id"),
            Student.name.label("student"),
            Event.title.label("event_title"),
            EventSubmission.submitted_at.label("submittedOn"),
            EventSubmission.status.label("status"),
        )
        .select_from(EventSubmission)
        .join(Student, Student.id == EventSubmission.student_id)
        .join(Event, Event.id == EventSubmission.event_id, isouter=True)
        .where(EventSubmission.submitted_at.isnot(None))
        .order_by(EventSubmission.submitted_at.desc(), EventSubmission.id.desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    submission_ids = [r.submission_id for r in rows]

    cert_map = {}

    if submission_ids:
        cert_q = await db.execute(
            select(
                Certificate.submission_id.label("submission_id"),
                Certificate.id.label("certificate_id"),
            ).where(Certificate.submission_id.in_(submission_ids))
        )

        cert_map = {
            r.submission_id: {
                "certificate_id": r.certificate_id,
            }
            for r in cert_q.all()
        }

    result = []

    for r in rows:
        event_title = (r.event_title or "").strip()

        if not event_title:
            event_title = f"Event #{r.event_id}" if r.event_id else "Unknown Event"

        cert = cert_map.get(r.submission_id)

        result.append(
            {
                "id": r.submission_id,
                "student": r.student,

                # ✅ actual event data
                "event_id": r.event_id,
                "event_title": event_title,
                "title": event_title,

                "category": "Event",
                "submittedOn": r.submittedOn.isoformat() if r.submittedOn else None,
                "status": str(getattr(r.status, "value", r.status)),

                # ✅ certificate generated or not
                "certificate": cert is not None,
                "certificate_id": cert.get("certificate_id") if cert else None,
            }
        )

    return result