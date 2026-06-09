from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.certificates.models import Certificate
from app.features.events.models import Event, EventActivityType
from app.features.activities.models import ActivityType
from app.features.students.models import Student
from app.features.submissions.models import Submission  # adjust path if different

from app.core.cert_sign import sign_cert
from app.core.cert_pdf import build_certificate_pdf
from app.core.cert_storage import upload_certificate_pdf_bytes

from app.features.events.service import _next_certificate_no as next_certificate_no


def _safe(value, fallback=""):
    return value if value not in (None, "") else fallback


def _format_event_month_year(event):
    """
    Tries to fetch event month/year from DB date fields.
    Update field names if your Event model uses different names.
    """
    dt = (
        getattr(event, "start_date", None)
        or getattr(event, "start_time", None)
        or getattr(event, "event_date", None)
        or getattr(event, "created_at", None)
    )

    if dt:
        return dt.strftime("%B %Y")

    return "the event period"


def _format_venue(event):
    return (
        getattr(event, "venue_name", None)
        or getattr(event, "venue", None)
        or getattr(event, "location", None)
        or getattr(event, "address", None)
        or "the event venue"
    )


def _get_student_college(student):
    return (
        getattr(student, "college_name", None)
        or getattr(student, "college", None)
        or getattr(student, "institution_name", None)
        or "the institution"
    )


def _get_event_title(event):
    return (
        getattr(event, "title", None)
        or getattr(event, "name", None)
        or "the activity"
    )


def _get_submission_points(submission, activity_type):
    """
    Prefer approved/final points from Submission.
    Fallback to ActivityType points.
    Adjust field names based on your actual models.
    """
    points = (
        getattr(submission, "points_awarded", None)
        or getattr(submission, "approved_points", None)
        or getattr(submission, "final_points", None)
        or getattr(submission, "points", None)
    )

    if points is not None:
        return int(points)

    fallback_points = (
        getattr(activity_type, "points_per_unit", None)
        or getattr(activity_type, "max_points", None)
        or 0
    )

    return int(fallback_points or 0)


async def generate_certificates_for_submission(
    db: AsyncSession,
    *,
    submission_id: int,
    student_id: int,
    event_id: int,
    academic_year: str,
):
    now = datetime.now(timezone.utc)

    # 1) Fetch student from DB
    student = (await db.execute(
        select(Student).where(Student.id == student_id)
    )).scalar_one_or_none()

    if not student:
        raise ValueError("Student not found.")

    # 2) Fetch event from DB
    event = (await db.execute(
        select(Event).where(Event.id == event_id)
    )).scalar_one_or_none()

    if not event:
        raise ValueError("Event not found.")

    # 3) Fetch submission from DB
    submission = (await db.execute(
        select(Submission).where(Submission.id == submission_id)
    )).scalar_one_or_none()

    if not submission:
        raise ValueError("Submission not found.")

    # 4) Get selected activity types for the event
    at_ids = (await db.execute(
        select(EventActivityType.activity_type_id)
        .where(EventActivityType.event_id == event_id)
        .order_by(EventActivityType.activity_type_id.asc())
    )).scalars().all()

    at_ids = list(dict.fromkeys([int(x) for x in at_ids if x]))

    if not at_ids:
        raise ValueError("No activity types configured for this event.")

    # 5) Load activity type objects
    activity_types = (await db.execute(
        select(ActivityType).where(ActivityType.id.in_(at_ids))
    )).scalars().all()

    activity_type_by_id = {a.id: a for a in activity_types}

    # 6) Fetch dynamic certificate fields from DB
    student_name = _safe(getattr(student, "name", None), "Student")
    usn = _safe(getattr(student, "usn", None), "")
    college_name = _get_student_college(student)

    event_title = _get_event_title(event)
    venue = _format_venue(event)
    event_month_year = _format_event_month_year(event)

    for at_id in at_ids:
        # idempotent: skip if already exists
        existing = (await db.execute(
            select(Certificate).where(
                Certificate.submission_id == submission_id,
                Certificate.activity_type_id == at_id,
            )
        )).scalar_one_or_none()

        if existing:
            continue

        activity_type = activity_type_by_id.get(at_id)

        if not activity_type:
            raise ValueError(f"Activity type {at_id} not found.")

        activity_type_name = _safe(
            getattr(activity_type, "name", None),
            f"Activity Type #{at_id}"
        )

        points_awarded = _get_submission_points(submission, activity_type)

        cert_no = await next_certificate_no(
            db,
            academic_year=academic_year,
            dt=now
        )

        signature = sign_cert(
            certificate_no=cert_no,
            student_id=student_id,
            event_id=event_id,
            activity_type_id=at_id,
        )

        pdf_bytes = build_certificate_pdf(
            certificate_no=cert_no,
            student_name=student_name,
            usn=usn,
            college_name=college_name,
            event_title=event_title,
            venue=venue,
            event_month_year=event_month_year,
            academic_year=academic_year,
            activity_type_name=activity_type_name,
            points=points_awarded,
            signature=signature,
            issued_date=now,
        )

        object_key = f"certificates/event_{event_id}/student_{student_id}/{cert_no}.pdf"

        await upload_certificate_pdf_bytes(
            object_key=object_key,
            pdf_bytes=pdf_bytes
        )

        db.add(Certificate(
            certificate_no=cert_no,
            issued_at=now,
            submission_id=submission_id,
            student_id=student_id,
            event_id=event_id,
            activity_type_id=at_id,
            pdf_path=object_key,
        ))

    await db.commit()