from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.certificates.models import Certificate
from app.features.events.models import EventActivityType
from app.features.activities.models import ActivityType
from app.core.cert_sign import sign_cert
from app.core.cert_pdf import build_certificate_pdf
from app.core.cert_storage import upload_certificate_pdf_bytes

from app.features.events.service import _next_certificate_no as next_certificate_no


async def generate_certificates_for_submission(
    db: AsyncSession,
    *,
    submission_id: int,
    student_id: int,
    event_id: int,
    academic_year: str,
):
    # 1) get selected activity types for the event
    at_ids = (await db.execute(
        select(EventActivityType.activity_type_id)
        .where(EventActivityType.event_id == event_id)
        .order_by(EventActivityType.activity_type_id.asc())
    )).scalars().all()

    at_ids = list(dict.fromkeys([int(x) for x in at_ids if x]))

    if not at_ids:
        raise ValueError("No activity types configured for this event.")

    # 2) load names
    ats = (await db.execute(select(ActivityType).where(ActivityType.id.in_(at_ids)))).scalars().all()
    name_by_id = {a.id: a.name for a in ats}

    now = datetime.now(timezone.utc)

    for at_id in at_ids:
        # ✅ idempotent: skip if already exists
        existing = (await db.execute(
            select(Certificate).where(
                Certificate.submission_id == submission_id,
                Certificate.activity_type_id == at_id,
            )
        )).scalar_one_or_none()
        if existing:
            continue

        cert_no = await next_certificate_no(db, academic_year=academic_year, dt=now)
        activity_type_name = name_by_id.get(at_id) or f"Activity Type #{at_id}"

        signature = sign_cert(
            certificate_no=cert_no,
            student_id=student_id,
            event_id=event_id,
            activity_type_id=at_id,
        )

        pdf_bytes = build_certificate_pdf(
            certificate_no=cert_no,
            student_id=student_id,
            event_id=event_id,
            activity_type_name=activity_type_name,
            signature=signature,
        )

        object_key = f"certificates/event_{event_id}/student_{student_id}/{cert_no}.pdf"
        await upload_certificate_pdf_bytes(object_key=object_key, pdf_bytes=pdf_bytes)

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