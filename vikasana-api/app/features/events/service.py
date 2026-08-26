
# app/controllers/events_controller.py
from __future__ import annotations
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date as date_type, time as time_type, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional
import secrets
from app.core.redis import cache_get, cache_set
from urllib.parse import quote
from app.features.activities.models import ActivityFaceCheck
from sqlalchemy import delete
from typing import List
from fastapi import HTTPException
from sqlalchemy import select, func, delete as sql_delete, update, cast, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.core.config import settings
from app.core.cert_sign import sign_cert
from app.core.cert_pdf import build_certificate_pdf
from app.core.cert_storage import (
    upload_certificate_pdf_bytes,
    presign_certificate_download_url,
)
from app.core.redis import cache_delete_pattern
import io
import os
import uuid
from fastapi import UploadFile
from app.core.minio_client import get_minio, ensure_bucket, get_presigned_url

from app.features.events.models import (
    Event,
    EventSubmission,
    EventSubmissionPhoto,
    EventParticipant,
    EventParticipantImportBatch,
)
from app.features.students.models import Student, StudentPushDevice
import boto3
from botocore.config import Config
from app.features.activities.models import StudentActivityStats

# ✅ Activity tracking
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType

# ✅ Event ↔ ActivityType mapping
from app.features.events.models import EventActivityType

# ✅ Certificates
from app.features.certificates.models import Certificate, CertificateCounter
from app.features.notifications.push_service import send_student_push_notifications
from app.features.events.role_guard import can_student_view_event


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
IST = ZoneInfo("Asia/Kolkata")

# ✅ Activity point rules
ACTIVITY_TYPE_POINT_CAP = 20

REGULAR_TOTAL_REQUIRED = 100
REGULAR_REQUIRED_ACTIVITY_TYPES = 5

DIPLOMA_TOTAL_REQUIRED = 60
DIPLOMA_REQUIRED_ACTIVITY_TYPES = 3


async def _send_certificate_ready_notifications(
    db: AsyncSession,
    *,
    event: Event,
    student_ids: list[int],
) -> dict[str, Any]:
    """
    Send one certificate-ready notification per student/event.

    Certificates must already be committed before this helper is called.
    Successful deliveries are deduped in student_notification_deliveries.
    """

    clean_student_ids = sorted({
        int(student_id)
        for student_id in student_ids
        if student_id is not None
    })

    if not clean_student_ids:
        return {
            "targets": 0,
            "successful_student_ids": [],
        }

    notification_type = "certificate_ready"

    dedupe_by_student = {
        student_id: (
            f"event:{event.id}:"
            f"student:{student_id}:"
            f"{notification_type}"
        )
        for student_id in clean_student_ids
    }

    dedupe_keys = list(dedupe_by_student.values())

    existing_result = await db.execute(
        text(
            """
            SELECT dedupe_key
            FROM student_notification_deliveries
            WHERE dedupe_key = ANY(:dedupe_keys)
            """
        ),
        {
            "dedupe_keys": dedupe_keys,
        },
    )

    existing_keys = {
        str(row[0])
        for row in existing_result.all()
    }

    target_student_ids = [
        student_id
        for student_id, dedupe_key
        in dedupe_by_student.items()
        if dedupe_key not in existing_keys
    ]

    if not target_student_ids:
        return {
            "targets": 0,
            "successful_student_ids": [],
            "already_notified": len(clean_student_ids),
        }

    push_result = await send_student_push_notifications(
        db,
        title="Certificate Generated 🎉",
        body=(
            f"Your certificate for {event.title} is ready. "
            f"Open LoRaa Connect to view/download it."
        ),
        data={
            "type": "certificate_ready",
            "event_id": event.id,
            "event_title": event.title,
            "route": "/(student)/certificates",
        },
        student_ids=target_student_ids,
    )

    successful_student_ids = [
        int(student_id)
        for student_id in (
            push_result.get(
                "successful_student_ids",
                [],
            )
            or []
        )
    ]

    scheduled_for = datetime.now(timezone.utc)

    for student_id in successful_student_ids:
        dedupe_key = dedupe_by_student[student_id]

        await db.execute(
            text(
                """
                INSERT INTO student_notification_deliveries (
                    student_id,
                    event_id,
                    notification_type,
                    dedupe_key,
                    status,
                    scheduled_for,
                    sent_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    :student_id,
                    :event_id,
                    :notification_type,
                    :dedupe_key,
                    'SENT',
                    :scheduled_for,
                    NOW(),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (dedupe_key)
                DO NOTHING
                """
            ),
            {
                "student_id": student_id,
                "event_id": event.id,
                "notification_type": notification_type,
                "dedupe_key": dedupe_key,
                "scheduled_for": scheduled_for,
            },
        )

    if successful_student_ids:
        await db.commit()

    print(
        "[Push Certificate Ready]",
        {
            "event_id": event.id,
            "targets": len(target_student_ids),
            "successful_student_ids": successful_student_ids,
        },
    )

    return {
        "targets": len(target_student_ids),
        "successful_student_ids": successful_student_ids,
        "push_result": push_result,
    }




# =========================================================
# ---------------------- PARSERS ---------------------------
# =========================================================

def _parse_date(val: Any) -> Optional[date_type]:
    """
    Accepts:
      - date
      - datetime
      - ISO string: "2026-03-01" or "2026-03-01T10:00:00"
    """
    if val is None:
        return None
    if isinstance(val, date_type) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return date_type.fromisoformat(s[:10])
        except Exception:
            return None
    return None


def _parse_time(val: Any) -> Optional[time_type]:
    """
    Accepts:
      - time
      - datetime (uses .time())
      - strings: "HH:MM", "HH:MM:SS", "HH:MM:SS.sss"
      - ISO datetime strings: "2026-03-01T12:22:00"
    Returns: datetime.time or None
    """
    if val is None:
        return None

    if isinstance(val, time_type) and not isinstance(val, datetime):
        return val.replace(tzinfo=None)

    if isinstance(val, datetime):
        return val.time().replace(tzinfo=None)

    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None

        # ISO datetime → extract time
        if "T" in s or " " in s:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt.time().replace(tzinfo=None)
            except Exception:
                pass

        # time-only: HH:MM[:SS[.ffffff]]
        try:
            return time_type.fromisoformat(s).replace(tzinfo=None)
        except Exception:
            pass

        # manual fallback
        try:
            parts = s.split(":")
            hh = int(parts[0])
            mm = int(parts[1]) if len(parts) > 1 else 0
            ss = int(float(parts[2])) if len(parts) > 2 else 0
            return time_type(hour=hh, minute=mm, second=ss)
        except Exception:
            return None

    return None


def _parse_datetime(val: Any) -> Optional[datetime]:
    """
    Accepts:
      - datetime
      - ISO string like:
          "2026-04-15T10:30:00+05:30"
          "2026-04-15T10:30:00Z"
          "2026-04-15 10:30:00"
    Returns:
      - timezone-aware datetime in IST
    """
    if val is None:
        return None

    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=IST)
        return val.astimezone(IST)

    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None

        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except Exception:
            return None

    return None

# # =========================================================
# ---------------------- TIME HELPERS ----------------------
# =========================================================
def _status_lower(col):
    """Normalize enum/string status columns to lowercase string for safe comparisons."""
    return func.lower(cast(col, String))

def _session_is_approved():
    """Treat APPROVED case-insensitively, compatible with enum/string storage."""
    return _status_lower(ActivitySession.status) == "approved"

def _submission_is_approved_or_expired():
    """EventSubmission can be approved or expired for certificate generation."""
    return _status_lower(EventSubmission.status).in_(["approved", "expired"])




IST = ZoneInfo("Asia/Kolkata")


def _now_ist_aware() -> datetime:
    """Current time in IST (timezone-aware)."""
    return datetime.now(IST)

def _to_ist_aware(dt: datetime) -> datetime:
    # treat naive as IST-local
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)

def _event_window_ist_aware(ev) -> tuple[datetime, datetime]:
    """
    Returns (start_ist, end_ist) as timezone-aware IST datetimes.

    ✅ UPDATED:
    - If end_time is NULL → default start + 24 hours
    - If end_time <= start_time → treat as next day
    """

    if not ev.event_date:
        raise ValueError("event_date missing")

    if not ev.start_time:
        raise ValueError("start_time missing")

    # ─────────────────────────────────────────────
    # Start datetime
    # ─────────────────────────────────────────────
    if isinstance(ev.start_time, datetime):
        start_dt = ev.start_time
    else:
        start_dt = datetime.combine(ev.event_date, ev.start_time)

    start_ist = _to_ist_aware(start_dt)

    # ─────────────────────────────────────────────
    # End datetime
    # ─────────────────────────────────────────────
    end_val = getattr(ev, "end_time", None)

    # ✅ if end_time not provided → default 24 hours
    if end_val is None:
        end_ist = start_ist + timedelta(hours=24)
        return start_ist, end_ist

    if isinstance(end_val, datetime):
        end_dt = end_val

    elif isinstance(end_val, time_type):
        end_dt = datetime.combine(ev.event_date, end_val)

        # if end <= start => next day
        if end_dt <= datetime.combine(ev.event_date, ev.start_time):
            end_dt = end_dt + timedelta(days=1)

    else:
        raise ValueError(f"invalid end_time type: {type(end_val)}")

    end_ist = _to_ist_aware(end_dt)

    return start_ist, end_ist

def _event_window_utc(event) -> tuple[datetime, datetime]:
    """
    Returns (start_utc, end_utc) as timezone-aware UTC datetimes.
    ✅ Use these for DB comparisons against timestamptz columns.
    """
    start_ist, end_ist = _event_window_ist_aware(event)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _ensure_event_window(event) -> None:
    """
    ✅ Unified window check using the SAME event window logic used for session filtering.
    Avoids naive datetime bugs and timezone mismatches.

    Raises:
      403 if event not active / not started / ended
      400 if window not configured
    """
    if not getattr(event, "is_active", True):
        raise HTTPException(status_code=403, detail="Event has ended.")

    start_ist, end_ist = _event_window_ist_aware(event)
    now_ist = _now_ist_aware()

    if now_ist < start_ist:
        raise HTTPException(status_code=403, detail="Event has not started yet.")

    if now_ist > end_ist:
        raise HTTPException(status_code=403, detail="Event has ended.")
    

def _next_missing_seq(uploaded: set[int], required_photos: int) -> int:
    for i in range(1, required_photos + 1):
        if i not in uploaded:
            return i
    return required_photos + 1  # means complete


async def get_student_event_draft_progress(db: AsyncSession, student_id: int, event_id: int) -> dict:
    """
    Returns draft progress so mobile app can resume:
    - which seq_no already uploaded
    - next_seq_no to capture
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    required_photos = int(getattr(event, "required_photos", 3) or 3)

    res = await db.execute(
        select(EventSubmission).where(
            EventSubmission.event_id == event_id,
            EventSubmission.student_id == student_id,
        )
    )
    sub = res.scalar_one_or_none()

    if not sub:
        return {
            "exists": False,
            "submission_id": None,
            "status": None,
            "required_photos": required_photos,
            "photo_capture_mode": getattr(event, "photo_capture_mode", None) or "normal",
            "event_date": event.event_date,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "uploaded_seq_nos": [],
            "next_seq_no": 1,
            "is_complete": False,
            "photos": [],
        }

    pres = await db.execute(
        select(EventSubmissionPhoto.seq_no, EventSubmissionPhoto.image_url)
        .where(EventSubmissionPhoto.submission_id == sub.id)
        .order_by(EventSubmissionPhoto.seq_no.asc())
    )
    rows = pres.all()

    uploaded_seq = {int(r[0]) for r in rows if r and r[0] is not None}
    next_seq = _next_missing_seq(uploaded_seq, required_photos)
    is_complete = next_seq > required_photos

    return {
        "exists": True,
        "submission_id": sub.id,
        "status": sub.status,
        "required_photos": required_photos,
        "photo_capture_mode": getattr(event, "photo_capture_mode", None) or "normal",
        "event_date": event.event_date,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "uploaded_seq_nos": sorted(uploaded_seq),
        "next_seq_no": next_seq,
        "is_complete": is_complete,
        "photos": [{"seq_no": int(r[0]), "image_url": r[1]} for r in rows],
    }

async def copy_event_photos_to_activity_session(
    db: AsyncSession,
    submission: EventSubmission,
    session: ActivitySession,
):
    q = await db.execute(
        select(EventSubmissionPhoto)
        .where(EventSubmissionPhoto.submission_id == submission.id)
        .order_by(EventSubmissionPhoto.seq_no.asc())
    )
    event_photos = q.scalars().all()

    if not event_photos:
        return

    for p in event_photos:
        existing = await db.execute(
            select(ActivityPhoto).where(
                ActivityPhoto.session_id == session.id,
                ActivityPhoto.seq_no == p.seq_no,
            )
        )
        already = existing.scalar_one_or_none()

        _in_geo = getattr(p, "is_in_geofence", None)
        is_in_geofence_val = bool(_in_geo) if _in_geo is not None else True

        if already:
            already.image_url = p.image_url
            already.lat = getattr(p, "lat", None)
            already.lng = getattr(p, "lng", None)
            already.captured_at = (
                getattr(p, "captured_at", None)
                or getattr(submission, "submitted_at", None)
                or datetime.now(timezone.utc)
            )
            already.distance_m = getattr(p, "distance_m", None)
            already.is_in_geofence = is_in_geofence_val
        else:
            db.add(
                ActivityPhoto(
                    session_id=session.id,
                    student_id=submission.student_id,
                    seq_no=p.seq_no,
                    image_url=p.image_url,
                    lat=getattr(p, "lat", None),
                    lng=getattr(p, "lng", None),
                    captured_at=(
                        getattr(p, "captured_at", None)
                        or getattr(submission, "submitted_at", None)
                        or datetime.now(timezone.utc)
                    ),
                    sha256=None,
                    distance_m=getattr(p, "distance_m", None),
                    is_in_geofence=is_in_geofence_val,
                    geo_flag_reason=None,
                )
            )

    await db.commit()

async def create_face_check_for_activity_session(
    db: AsyncSession,
    submission: EventSubmission,
    session: ActivitySession,
):
    """
    Create a basic ActivityFaceCheck so Activity Sessions UI can show a face image.

    Uses the first activity photo as the face-check photo.
    """
    photo_res = await db.execute(
        select(ActivityPhoto)
        .where(ActivityPhoto.session_id == session.id)
        .order_by(ActivityPhoto.seq_no.asc())
    )
    activity_photos = photo_res.scalars().all()

    if not activity_photos:
        return

    chosen_photo = activity_photos[0]

    existing_res = await db.execute(
        select(ActivityFaceCheck).where(
            ActivityFaceCheck.session_id == session.id,
            ActivityFaceCheck.photo_id == chosen_photo.id,
        )
    )
    existing = existing_res.scalar_one_or_none()

    if existing:
        if not existing.raw_image_url:
            existing.raw_image_url = chosen_photo.image_url
        if existing.student_id != submission.student_id:
            existing.student_id = submission.student_id
        if existing.total_faces is None:
            existing.total_faces = 1
    else:
        db.add(
            ActivityFaceCheck(
                student_id=submission.student_id,
                session_id=session.id,
                photo_id=chosen_photo.id,
                matched=False,
                cosine_score=0.0,
                l2_score=0.0,
                total_faces=1,
                raw_image_url=chosen_photo.image_url,
                processed_object=None,
                reason="event_submission_import",
            )
        )

    await db.commit()

async def _photo_window_for_submission(
    db: AsyncSession,
    submission_id: int,
    event: Event,
) -> tuple[datetime, datetime, float]:
    """
    Calculates verified hours using:
    first photo captured_at -> last photo captured_at

    Also clamps the time inside event start/end window.
    """

    event_start_utc, event_end_utc = _event_window_utc(event)

    q = await db.execute(
        select(EventSubmissionPhoto)
        .where(EventSubmissionPhoto.submission_id == submission_id)
        .order_by(EventSubmissionPhoto.seq_no.asc())
    )
    photos = q.scalars().all()

    times: list[datetime] = []

    for p in photos:
        dt = getattr(p, "captured_at", None)

        # fallback for old photos before captured_at column existed
        if dt is None:
            dt = getattr(p, "created_at", None)

        if dt is None:
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        times.append(dt.astimezone(timezone.utc))

    # Need at least 2 photos to calculate duration
    if len(times) < 2:
        return event_start_utc, event_start_utc, 0.0

    first_photo_utc = min(times)
    last_photo_utc = max(times)

    # safety: don't allow photo time outside actual event window
    verified_start = max(first_photo_utc, event_start_utc)
    verified_end = min(last_photo_utc, event_end_utc)

    verified_hours = max(
        0.0,
        (verified_end - verified_start).total_seconds() / 3600.0,
    )

    return verified_start, verified_end, verified_hours

async def create_or_update_activity_session_from_submission(
    db: AsyncSession,
    submission: EventSubmission,
    event: Event,
    target_status: ActivitySessionStatus,
):
    activity_type_ids = await _get_event_activity_type_ids(db, event.id)
    if not activity_type_ids:
        return []

    # ✅ Now using first photo timestamp → last photo timestamp
    start_utc, end_utc, verified_hours = await _photo_window_for_submission(
        db=db,
        submission_id=submission.id,
        event=event,
    )

    now_utc = datetime.now(timezone.utc)
    sessions = []

    for at_id in activity_type_ids:
        # First try the permanent event-submission linkage.
        q = await db.execute(
            select(ActivitySession)
            .where(
                ActivitySession.event_submission_id == submission.id,
                ActivitySession.activity_type_id == at_id,
            )
            .order_by(ActivitySession.id.desc())
        )

        session = q.scalars().first()

        # Legacy fallback for rows created before event linkage existed.
        if session is None:
            q = await db.execute(
                select(ActivitySession)
                .where(
                    ActivitySession.student_id == submission.student_id,
                    ActivitySession.activity_type_id == at_id,
                    ActivitySession.started_at <= end_utc,
                    func.coalesce(
                        ActivitySession.expires_at,
                        ActivitySession.submitted_at,
                        end_utc,
                    ) >= start_utc,
                )
                .order_by(ActivitySession.id.desc())
            )

            session = q.scalars().first()

        if session:
            # Backfill linkage whenever an older event session is encountered.
            session.event_id = event.id
            session.event_submission_id = submission.id

            session.status = target_status

            if target_status in [
                ActivitySessionStatus.SUBMITTED,
                ActivitySessionStatus.APPROVED,
            ]:
                if session.submitted_at is None:
                    session.submitted_at = (
                        getattr(submission, "submitted_at", None) or now_utc
                    )

            # ✅ Update session time using photo window
            session.started_at = start_utc
            session.expires_at = end_utc
            session.duration_hours = verified_hours

        else:
            session = ActivitySession(
                student_id=submission.student_id,
                activity_type_id=at_id,
                event_id=event.id,
                event_submission_id=submission.id,
                activity_name=getattr(event, "title", "Event Activity"),
                description=getattr(submission, "description", None),
                session_code=secrets.token_hex(8),

                # ✅ First photo time
                started_at=start_utc,

                # ✅ Last photo time
                expires_at=end_utc,

                submitted_at=(
                    getattr(submission, "submitted_at", None) or now_utc
                    if target_status in [
                        ActivitySessionStatus.SUBMITTED,
                        ActivitySessionStatus.APPROVED,
                    ]
                    else None
                ),
                status=target_status,

                # ✅ Duration from first photo to last photo
                duration_hours=verified_hours,
            )
            db.add(session)
            await db.flush()

        sessions.append(session)

    await db.commit()
    return sessions

# =========================================================
# ---------------------- CERT HELPERS ----------------------
# =========================================================

def _month_code(dt: datetime) -> str:
    return dt.strftime("%b")  # Jan, Feb...


def _academic_year_from_date(dt: datetime) -> str:
    """
    Academic year in India typically: Jun -> May
    Example:
      Feb 2025 => 2024-25
      Jul 2025 => 2025-26
    """
    y = dt.year
    m = dt.month
    start_year = y if m >= 6 else (y - 1)
    end_year_short = str(start_year + 1)[-2:]
    return f"{start_year}-{end_year_short}"


async def _next_certificate_no(db: AsyncSession, academic_year: str, dt: datetime) -> str:
    """
    BG/VF/{MONTH_CODE}{SEQ}/{ACADEMIC_YEAR}
    Example: BG/VF/Jan619/2024-25
    Uses row lock to avoid duplicate seq in concurrent generations.
    """
    m = _month_code(dt)

    stmt = (
        select(CertificateCounter)
        .where(
            CertificateCounter.month_code == m,
            CertificateCounter.academic_year == academic_year,
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    counter = res.scalar_one_or_none()

    if counter is None:
        counter = CertificateCounter(month_code=m, academic_year=academic_year, next_seq=1)
        db.add(counter)
        await db.flush()

    seq = int(counter.next_seq or 1)
    counter.next_seq = seq + 1
    counter.updated_at = datetime.now(timezone.utc)

    return f"BG/VF/{m}{seq}/{academic_year}"


async def _get_event_activity_type_ids(db: AsyncSession, event_id: int) -> list[int]:
    aq = await db.execute(
        select(EventActivityType.activity_type_id).where(EventActivityType.event_id == event_id)
    )
    return [int(r[0]) for r in aq.all() if r and r[0] is not None]

async def _calculate_submission_points(
    db: AsyncSession,
    submission: EventSubmission,
    event: Event,
) -> tuple[int, dict[int, dict]]:
    """
    Supports two scoring modes:
    - AUTO   -> ActivityType.hours_per_unit + points_per_unit
    - MANUAL -> EventActivityType.manual_points

    Rule:
    - Each activity type can give maximum 20 points lifetime per student.
    """

    # Authoritative duration for THIS submission only:
    # first captured photo -> last captured photo,
    # clamped inside the event window.
    _, _, submission_verified_hours = await _photo_window_for_submission(
        db=db,
        submission_id=submission.id,
        event=event,
    )

    total_points = 0
    breakdown: dict[int, dict] = {}

    map_q = await db.execute(
        select(EventActivityType, ActivityType)
        .join(ActivityType, ActivityType.id == EventActivityType.activity_type_id)
        .where(EventActivityType.event_id == event.id)
    )
    rows = map_q.all()

    if not rows:
        return 0, {}

    activity_type_ids = [int(mapping.activity_type_id) for mapping, _ in rows]

    stats_q = await db.execute(
        select(StudentActivityStats).where(
            StudentActivityStats.student_id == submission.student_id,
            StudentActivityStats.activity_type_id.in_(activity_type_ids),
        )
    )

    stats_by_type = {
        int(s.activity_type_id): s
        for s in stats_q.scalars().all()
        if s.activity_type_id is not None
    }

    for mapping, at in rows:
        at_id = int(mapping.activity_type_id)

        # Every activity type mapped to this event receives the same
        # verified duration from THIS submission only.
        hours = max(
            0.0,
            float(submission_verified_hours or 0.0),
        )

        # ✅ Always force max 20 points per activity type
        configured_max = getattr(at, "max_points", None)

        try:
            max_points = int(configured_max) if configured_max is not None else ACTIVITY_TYPE_POINT_CAP
        except Exception:
            max_points = ACTIVITY_TYPE_POINT_CAP

        max_points = min(max_points, ACTIVITY_TYPE_POINT_CAP)

        score_mode = str(getattr(mapping, "score_mode", "AUTO") or "AUTO").upper()
        min_required_hours = float(getattr(mapping, "min_required_hours", 0) or 0)

        stats_row = stats_by_type.get(at_id)

        previous_hours = float(
            getattr(stats_row, "total_verified_hours", 0.0) or 0.0
        )

        already_awarded = int(
            getattr(stats_row, "points_awarded", 0) or 0
        )

        remaining_cap = max(
            0,
            int(max_points) - already_awarded,
        )

        raw_points = 0
        target_total_points = already_awarded
        points_to_award = 0

        # Contribution to cumulative progress for THIS event.
        #
        # AUTO:
        #   actual verified first-photo -> last-photo hours
        #
        # MANUAL:
        #   configured points are converted back to equivalent hours.
        #   With 4 hrs = 1 point:
        #       5 manual points -> 20 equivalent hours.
        contribution_hours = max(0.0, hours)

        if score_mode == "MANUAL":
            manual_points = 0

            if hours > 0 and hours >= min_required_hours:
                manual_points = max(
                    0,
                    int(
                        getattr(
                            mapping,
                            "manual_points",
                            0,
                        ) or 0
                    ),
                )

            ppu = float(
                getattr(at, "points_per_unit", 0) or 0
            )
            hpu = float(
                getattr(at, "hours_per_unit", 0) or 0
            )

            if manual_points > 0 and ppu > 0 and hpu > 0:
                contribution_hours = (
                    float(manual_points) *
                    (hpu / ppu)
                )
            else:
                contribution_hours = 0.0

            cumulative_hours = max(
                0.0,
                previous_hours + contribution_hours,
            )

            if ppu > 0 and hpu > 0:
                try:
                    completed_units = int(
                        cumulative_hours // hpu
                    )

                    target_total_points = min(
                        int(max_points),
                        completed_units * int(ppu),
                    )
                except Exception:
                    target_total_points = already_awarded

            target_total_points = max(
                already_awarded,
                int(target_total_points),
            )

            points_to_award = max(
                0,
                target_total_points - already_awarded,
            )

            points_to_award = min(
                points_to_award,
                remaining_cap,
            )

            raw_points = target_total_points

        else:
            cumulative_hours = max(
                0.0,
                previous_hours + contribution_hours,
            )
            # AUTO scoring is cumulative:
            #
            # 0.00 - 3.99 hrs  -> 0 points
            # 4.00 - 7.99 hrs  -> 1 point
            # 8.00 - 11.99 hrs -> 2 points
            # ...
            #
            # With the current ActivityType configuration:
            # 4 hours = 1 point, maximum 20 points.
            ppu = getattr(at, "points_per_unit", None)
            hpu = getattr(at, "hours_per_unit", None)

            if ppu is not None and hpu:
                try:
                    completed_units = int(
                        cumulative_hours // float(hpu)
                    )

                    target_total_points = min(
                        int(max_points),
                        completed_units * int(ppu),
                    )
                except Exception:
                    target_total_points = already_awarded

            target_total_points = max(
                already_awarded,
                int(target_total_points),
            )

            points_to_award = max(
                0,
                target_total_points - already_awarded,
            )

            points_to_award = min(
                points_to_award,
                remaining_cap,
            )

            raw_points = target_total_points

        breakdown[at_id] = {
            "hours": hours,
            "contribution_hours": contribution_hours,
            "previous_hours": previous_hours,
            "cumulative_hours": cumulative_hours,
            "score_mode": score_mode,
            "raw_points": raw_points,
            "already_awarded": already_awarded,
            "target_total_points": target_total_points,
            "remaining_cap": remaining_cap,
            "points_to_award": points_to_award,
            "max_points": max_points,
        }

        total_points += points_to_award

    return total_points, breakdown

async def _credit_submission_points_once(
    db: AsyncSession,
    submission: EventSubmission,
    event: Event,
) -> int:
    """
    Credits points only once per submission.

    Rule:
    - Each activity type can give maximum 20 points lifetime per student.
    - completed_at is set only when that activity type reaches 20/20.
    """
    if bool(getattr(submission, "points_credited", False)):
        return int(getattr(submission, "awarded_points", 0) or 0)

    total_points, breakdown = await _calculate_submission_points(db, submission, event)

    student = await db.get(Student, submission.student_id)
    if not student:
        return 0

    now_utc = datetime.now(timezone.utc)

    for at_id, data in breakdown.items():
        pts = int(data.get("points_to_award", 0) or 0)
        hrs = float(data.get("hours", 0.0) or 0.0)

        cumulative_hours = float(
            data.get("cumulative_hours", hrs) or 0.0
        )

        target_total_points = int(
            data.get("target_total_points", pts) or 0
        )

        if pts <= 0 and hrs <= 0:
            continue

        stats_q = await db.execute(
            select(StudentActivityStats).where(
                StudentActivityStats.student_id == submission.student_id,
                StudentActivityStats.activity_type_id == int(at_id),
            )
        )
        stats_row = stats_q.scalar_one_or_none()

        final_points_for_type = min(
            ACTIVITY_TYPE_POINT_CAP,
            max(0, target_total_points),
        )

        if stats_row is None:
            stats_row = StudentActivityStats(
                student_id=submission.student_id,
                activity_type_id=int(at_id),
                total_verified_hours=max(
                    0.0,
                    cumulative_hours,
                ),
                points_awarded=final_points_for_type,
                completed_at=(
                    now_utc
                    if final_points_for_type >= ACTIVITY_TYPE_POINT_CAP
                    else None
                ),
            )
            db.add(stats_row)

        else:
            # Store the true accumulated verified hours for this
            # activity type across approved events.
            stats_row.total_verified_hours = max(
                0.0,
                cumulative_hours,
            )

            stats_row.points_awarded = final_points_for_type

            if (
                final_points_for_type >= ACTIVITY_TYPE_POINT_CAP
                and not getattr(stats_row, "completed_at", None)
            ):
                stats_row.completed_at = now_utc

    student.total_points_earned = int(student.total_points_earned or 0) + int(total_points)

    submission.awarded_points = int(total_points)
    submission.points_credited = True

    if getattr(submission, "approved_at", None) is None:
        submission.approved_at = now_utc

    await db.commit()
    return total_points


def _is_diploma_student(student: Student) -> bool:
    """
    Detect diploma student safely.
    Change field names if your Student model has a specific column.
    """
    possible_fields = [
        "student_type",
        "course_type",
        "program_type",
        "admission_type",
        "degree_type",
    ]

    for field in possible_fields:
        value = getattr(student, field, None)
        if value and "diploma" in str(value).lower():
            return True

    return False


async def get_student_activity_progress(db: AsyncSession, student_id: int) -> dict:
    student = await db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    is_diploma = _is_diploma_student(student)

    required_total_points = DIPLOMA_TOTAL_REQUIRED if is_diploma else REGULAR_TOTAL_REQUIRED
    required_activity_types = DIPLOMA_REQUIRED_ACTIVITY_TYPES if is_diploma else REGULAR_REQUIRED_ACTIVITY_TYPES
    student_type = "DIPLOMA" if is_diploma else "REGULAR"

    # Get all activity types
    at_q = await db.execute(
        select(ActivityType).order_by(ActivityType.name.asc())
    )
    activity_types = at_q.scalars().all()

    # Get this student's points per activity type
    stats_q = await db.execute(
        select(StudentActivityStats).where(
            StudentActivityStats.student_id == student_id
        )
    )
    stats_rows = stats_q.scalars().all()

    stats_by_type = {
        int(s.activity_type_id): s
        for s in stats_rows
        if s.activity_type_id is not None
    }

    items = []
    total_points_raw = 0
    completed_activity_types = 0

    for at in activity_types:
        at_id = int(at.id)
        stats = stats_by_type.get(at_id)

        earned = int(getattr(stats, "points_awarded", 0) or 0) if stats else 0
        earned = min(earned, ACTIVITY_TYPE_POINT_CAP)

        remaining = max(0, ACTIVITY_TYPE_POINT_CAP - earned)

        if earned >= ACTIVITY_TYPE_POINT_CAP:
            status = "COMPLETED"
            completed_activity_types += 1
        elif earned > 0:
            status = "IN_PROGRESS"
        else:
            status = "NOT_STARTED"

        total_points_raw += earned

        items.append({
            "activity_type_id": at_id,
            "activity_type_name": getattr(at, "name", None) or f"Activity Type #{at_id}",
            "earned_points": earned,
            "max_points": ACTIVITY_TYPE_POINT_CAP,
            "remaining_points": remaining,
            "status": status,
            "total_verified_hours": (
                float(getattr(stats, "total_verified_hours", 0.0) or 0.0)
                if stats else 0.0
            ),
            "completed_at": getattr(stats, "completed_at", None) if stats else None,
        })

    display_total_points = min(total_points_raw, required_total_points)

    return {
        "student_id": student_id,
        "student_type": student_type,
        "required_total_points": required_total_points,
        "required_activity_types": required_activity_types,
        "max_points_per_activity_type": ACTIVITY_TYPE_POINT_CAP,

        "total_points": display_total_points,
        "actual_total_points": total_points_raw,
        "remaining_total_points": max(0, required_total_points - display_total_points),

        "completed_activity_types": completed_activity_types,
        "remaining_activity_types": max(0, required_activity_types - completed_activity_types),

        "is_requirement_completed": (
            display_total_points >= required_total_points
            and completed_activity_types >= required_activity_types
        ),

        "activity_types": items,
    }

async def _eligible_students_from_sessions(
    db: AsyncSession,
    event: Event,
    activity_type_ids: list[int],
) -> list[int]:
    """
    ✅ Students eligible for auto-approval:
    - Have APPROVED ActivitySession (case-insensitive)
    - Session.activity_type_id in event mapped ids
    - Session overlaps the event window (not just started_at inside)
      overlap condition:
        started_at <= end_utc AND session_end >= start_utc
      where session_end = coalesce(submitted_at, expires_at, end_utc)
    """

    if not activity_type_ids:
        return []

    start_utc, end_utc = _event_window_utc(event)
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    session_end = func.coalesce(
        ActivitySession.expires_at,
        ActivitySession.submitted_at,
        end_utc,  # ✅ fallback so NULL doesn't break overlap logic
    )

    q = await db.execute(
        select(func.distinct(ActivitySession.student_id)).where(
            func.lower(cast(ActivitySession.status, String)) == "approved",
            ActivitySession.activity_type_id.in_(activity_type_ids),

            # ✅ overlap (same as certificate logic)
            ActivitySession.started_at <= end_utc,
            session_end >= start_utc,
        )
    )

    return [int(r[0]) for r in q.all() if r and r[0] is not None]

async def auto_approve_event_from_sessions(db: AsyncSession, event_id: int) -> dict:
    """
    ✅ MAIN BUTTON LOGIC (Top approve):
    - Finds students with APPROVED sessions overlapping the event window for mapped activity types
    - Upserts EventSubmission => status="approved", sets submitted_at + approved_at
    - Credits points immediately after approval
    - Does NOT generate certificates automatically
    """

    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Event window (UTC)
    start_utc, end_utc = _event_window_utc(event)
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    # ✅ Try mapped activity types first
    mapped_ids = await _get_event_activity_type_ids(db, event_id)
    activity_type_ids = sorted({int(x) for x in mapped_ids if x is not None})

    # ✅ FALLBACK: infer activity types from APPROVED sessions OVERLAPPING the window
    if not activity_type_ids:
        session_end = func.coalesce(
            ActivitySession.expires_at,
            ActivitySession.submitted_at,
            end_utc,
        )

        aq = await db.execute(
            select(func.distinct(ActivitySession.activity_type_id)).where(
                func.lower(cast(ActivitySession.status, String)) == "approved",
                ActivitySession.activity_type_id.is_not(None),
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )
        activity_type_ids = sorted(
            {int(r[0]) for r in aq.all() if r and r[0] is not None}
        )

    if not activity_type_ids:
        return {
            "event_id": event_id,
            "eligible_students": 0,
            "submissions_approved": 0,
            "certificates_issued": 0,
            "points_credited_total": 0,
        }

    eligible_student_ids = await _eligible_students_from_sessions(
        db,
        event,
        activity_type_ids,
    )
    eligible_student_ids = sorted(
        {int(x) for x in (eligible_student_ids or []) if x is not None}
    )

    if not eligible_student_ids:
        return {
            "event_id": event_id,
            "eligible_students": 0,
            "submissions_approved": 0,
            "certificates_issued": 0,
            "points_credited_total": 0,
        }

    now_utc = datetime.now(timezone.utc)
    submissions_approved = 0

    for sid in eligible_student_ids:
        res = await db.execute(
            select(EventSubmission).where(
                EventSubmission.event_id == event_id,
                EventSubmission.student_id == sid,
            )
        )
        sub = res.scalar_one_or_none()

        if sub is None:
            sub = EventSubmission(
                event_id=event_id,
                student_id=sid,
                status="approved",
            )

            if hasattr(sub, "submitted_at"):
                sub.submitted_at = now_utc

            if hasattr(sub, "approved_at"):
                sub.approved_at = now_utc

            db.add(sub)
            submissions_approved += 1

        else:
            if (sub.status or "").lower() != "approved":
                sub.status = "approved"

                if hasattr(sub, "submitted_at") and getattr(sub, "submitted_at", None) is None:
                    sub.submitted_at = now_utc

                if hasattr(sub, "approved_at"):
                    sub.approved_at = now_utc

                submissions_approved += 1

    await db.commit()

    # ✅ NEW: credit points after auto-approval
    points_credited_total = 0

    for sid in eligible_student_ids:
        sub_q = await db.execute(
            select(EventSubmission).where(
                EventSubmission.event_id == event_id,
                EventSubmission.student_id == sid,
            )
        )
        sub = sub_q.scalar_one_or_none()

        if sub:
            points_credited_total += await _credit_submission_points_once(
                db=db,
                submission=sub,
                event=event,
            )

    return {
        "event_id": event_id,
        "eligible_students": len(eligible_student_ids),
        "submissions_approved": submissions_approved,
        "certificates_issued": 0,
        "points_credited_total": points_credited_total,
    }

async def _infer_activity_type_ids_from_sessions(
    db: AsyncSession,
    start_utc: datetime,
    end_utc: datetime,
) -> list[int]:
    """
    Infer activity types from APPROVED sessions overlapping the event window.

    ✅ FIX:
    - Uses session_end fallback = coalesce(submitted_at, expires_at, end_utc)
      so NULL end timestamps don't kill overlap filters.
    - Uses case-insensitive APPROVED match.
    """
    session_end = func.coalesce(
        ActivitySession.expires_at,
        ActivitySession.submitted_at,
        end_utc,
    )

    aq = await db.execute(
        select(func.distinct(ActivitySession.activity_type_id)).where(
            _session_is_approved(),
            ActivitySession.activity_type_id.is_not(None),

            # ✅ overlap logic
            ActivitySession.started_at <= end_utc,
            session_end >= start_utc,
        )
    )
    return [int(r[0]) for r in aq.all() if r and r[0] is not None]

async def _certificate_points_for_event_activity(
    db: AsyncSession,
    event_id: int,
    at,
    at_id: int,
    hours: float,
) -> int:
    map_q = await db.execute(
        select(EventActivityType).where(
            EventActivityType.event_id == event_id,
            EventActivityType.activity_type_id == at_id,
        )
    )
    mapping = map_q.scalar_one_or_none()

    score_mode = str(getattr(mapping, "score_mode", "AUTO") or "AUTO").upper()
    min_required_hours = float(getattr(mapping, "min_required_hours", 0) or 0)

    if hours < min_required_hours:
        return 0

    if score_mode == "MANUAL":
        return max(0, int(getattr(mapping, "manual_points", 0) or 0))

    ppu = getattr(at, "points_per_unit", None)
    hpu = getattr(at, "hours_per_unit", None)

    if ppu is not None and hpu:
        try:
            completed_units = int(
                float(hours) // float(hpu)
            )

            return max(
                0,
                completed_units * int(ppu),
            )
        except Exception:
            return 0

    return 0



async def _issue_certificates_for_event(db: AsyncSession, event: Event) -> int:
    """
    Generate certificates ONLY for approved submissions of this event.

    Rules:
    - Only EventSubmission.status == "approved"
    - ActivitySession must still be approved
    - Certificate points follow event scoring mode:
        AUTO   -> hours formula
        MANUAL -> manual_points
    - Certificate is created only when admin triggers generation
    """

    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.event_id == event.id,
            func.lower(cast(EventSubmission.status, String)) == "approved",
        )
    )
    submissions = q.scalars().all()
    if not submissions:
        return 0

    start_utc, end_utc = _event_window_utc(event)
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    mapped_ids = await _get_event_activity_type_ids(db, event.id)
    activity_type_ids = sorted({int(x) for x in mapped_ids if x is not None})

    if not activity_type_ids:
        activity_type_ids = await _infer_activity_type_ids_from_sessions(db, start_utc, end_utc)

    if not activity_type_ids:
        raise HTTPException(
            status_code=400,
            detail="No activity types found for this event (mapping empty and no approved sessions in event window).",
        )

    now_utc = datetime.now(timezone.utc)
    now_ist = _now_ist_aware()
    academic_year = _academic_year_from_date(now_ist)

    venue_name = (
        getattr(event, "venue_name", None)
        or getattr(event, "venue", None)
        or getattr(event, "location", None)
        or ""
    ).strip() or "N/A"

    student_ids = sorted({int(s.student_id) for s in submissions if s.student_id is not None})
    st_q = await db.execute(select(Student).where(Student.id.in_(student_ids)))
    students = st_q.scalars().all()
    student_by_id = {int(s.id): s for s in students}

    at_q = await db.execute(select(ActivityType).where(ActivityType.id.in_(activity_type_ids)))
    ats = at_q.scalars().all()
    at_by_id = {int(a.id): a for a in ats}

    async def _hours_in_window(student_id: int, at_id: int) -> float:
        session_end = func.coalesce(
            ActivitySession.expires_at,
            ActivitySession.submitted_at,
            end_utc,
        )

        hrs_q = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        func.greatest(
                            0.0,
                            func.extract(
                                "epoch",
                                (
                                    func.least(session_end, end_utc)
                                    - func.greatest(ActivitySession.started_at, start_utc)
                                ),
                            ) / 3600.0,
                        )
                    ),
                    0.0,
                )
            ).where(
                ActivitySession.student_id == student_id,
                ActivitySession.activity_type_id == at_id,
                func.lower(cast(ActivitySession.status, String)) == "approved",
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )
        return float(hrs_q.scalar() or 0.0)

    issued = 0
    issued_student_ids: set[int] = set()

    for sub in submissions:
        if sub.student_id is None:
            continue

        student = student_by_id.get(int(sub.student_id))
        if not student:
            continue

        student_name = (getattr(student, "name", None) or "Student").strip()
        usn = (getattr(student, "usn", None) or "").strip()

        for at_id in activity_type_ids:
            at_id = int(at_id)

            ex = await db.execute(
                select(Certificate.id).where(
                    Certificate.submission_id == sub.id,
                    Certificate.activity_type_id == at_id,
                )
            )
            if ex.scalar_one_or_none():
                continue

            hours = await _hours_in_window(int(sub.student_id), at_id)
            if hours <= 0:
                continue

            at = at_by_id.get(at_id)
            activity_type_name = (getattr(at, "name", None) or "").strip() or f"Activity Type #{at_id}"

            points_awarded = await _certificate_points_for_event_activity(
                db=db,
                event_id=event.id,
                at=at,
                at_id=at_id,
                hours=hours,
            )

            cert_no = await _next_certificate_no(db, academic_year, now_ist)

            cert = Certificate(
                certificate_no=cert_no,
                submission_id=sub.id,
                student_id=sub.student_id,
                event_id=event.id,
                activity_type_id=at_id,
                issued_at=now_utc,
            )
            db.add(cert)
            await db.flush()

            sig = sign_cert(cert.certificate_no)
            verify_url = (
                f"{settings.PUBLIC_BASE_URL}/api/public/certificates/verify"
                f"?cert_id={quote(cert.certificate_no)}&sig={quote(sig)}"
            )

            pdf_bytes = build_certificate_pdf(
                template_pdf_path=settings.CERT_TEMPLATE_PDF_PATH,
                certificate_no=cert.certificate_no,
                issue_date=(cert.issued_at.date().isoformat() if cert.issued_at else now_ist.date().isoformat()),
                student_name=student_name,
                usn=usn,
                activity_type=activity_type_name,
                venue_name=venue_name,
                activity_points=int(points_awarded),
                verify_url=verify_url,
            )

            object_key = upload_certificate_pdf_bytes(cert.id, pdf_bytes)
            cert.pdf_path = object_key

            issued += 1
            issued_student_ids.add(int(sub.student_id))

    if issued == 0 and mapped_ids:
        inferred_ids = await _infer_activity_type_ids_from_sessions(db, start_utc, end_utc)
        inferred_ids = sorted({int(i) for i in inferred_ids if i is not None and int(i) > 0})
        inferred_ids = [i for i in inferred_ids if i not in activity_type_ids]

        if inferred_ids:
            at_q2 = await db.execute(select(ActivityType).where(ActivityType.id.in_(inferred_ids)))
            for a in at_q2.scalars().all():
                at_by_id[int(a.id)] = a

            for sub in submissions:
                if sub.student_id is None:
                    continue

                student = student_by_id.get(int(sub.student_id))
                if not student:
                    continue

                student_name = (getattr(student, "name", None) or "Student").strip()
                usn = (getattr(student, "usn", None) or "").strip()

                for at_id in inferred_ids:
                    at_id = int(at_id)

                    ex = await db.execute(
                        select(Certificate.id).where(
                            Certificate.submission_id == sub.id,
                            Certificate.activity_type_id == at_id,
                        )
                    )
                    if ex.scalar_one_or_none():
                        continue

                    hours = await _hours_in_window(int(sub.student_id), at_id)
                    if hours <= 0:
                        continue

                    at = at_by_id.get(at_id)
                    activity_type_name = (getattr(at, "name", None) or "").strip() or f"Activity Type #{at_id}"

                    points_awarded = await _certificate_points_for_event_activity(
                        db=db,
                        event_id=event.id,
                        at=at,
                        at_id=at_id,
                        hours=hours,
                    )

                    cert_no = await _next_certificate_no(db, academic_year, now_ist)

                    cert = Certificate(
                        certificate_no=cert_no,
                        submission_id=sub.id,
                        student_id=sub.student_id,
                        event_id=event.id,
                        activity_type_id=at_id,
                        issued_at=now_utc,
                    )
                    db.add(cert)
                    await db.flush()

                    sig = sign_cert(cert.certificate_no)
                    verify_url = (
                        f"{settings.PUBLIC_BASE_URL}/api/public/certificates/verify"
                        f"?cert_id={quote(cert.certificate_no)}&sig={quote(sig)}"
                    )

                    pdf_bytes = build_certificate_pdf(
                        template_pdf_path=settings.CERT_TEMPLATE_PDF_PATH,
                        certificate_no=cert.certificate_no,
                        issue_date=(cert.issued_at.date().isoformat() if cert.issued_at else now_ist.date().isoformat()),
                        student_name=student_name,
                        usn=usn,
                        activity_type=activity_type_name,
                        venue_name=venue_name,
                        activity_points=int(points_awarded),
                        verify_url=verify_url,
                    )

                    object_key = upload_certificate_pdf_bytes(cert.id, pdf_bytes)
                    cert.pdf_path = object_key

                    issued += 1
                    issued_student_ids.add(int(sub.student_id))

    await db.commit()

    if issued_student_ids:
        try:
            await _send_certificate_ready_notifications(
                db,
                event=event,
                student_ids=sorted(issued_student_ids),
            )
        except Exception as push_error:
            await db.rollback()
            print(
                "[Push Certificate Ready] Failed after certificate commit:",
                {
                    "event_id": event.id,
                    "error": repr(push_error),
                },
            )

    return issued

async def generate_missing_event_certificates_batch(
    db: AsyncSession,
    event_id: int,
    limit: int = 100,
) -> dict:
    """
    Generate only missing certificates in small batches.

    This avoids browser/API timeout for large events like 1000+ participants.
    Does NOT delete existing certificates.
    """

    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    limit = min(max(int(limit or 100), 1), 200)

    start_utc, end_utc = _event_window_utc(event)
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    mapped_ids = await _get_event_activity_type_ids(db, event.id)
    activity_type_ids = sorted({int(x) for x in mapped_ids if x is not None})

    if not activity_type_ids:
        activity_type_ids = await _infer_activity_type_ids_from_sessions(
            db,
            start_utc,
            end_utc,
        )

    if not activity_type_ids:
        raise HTTPException(
            status_code=400,
            detail="No activity types found for this event.",
        )

    # Get approved submissions which do not already have certs for all activity types
    sub_q = await db.execute(
        select(EventSubmission)
        .where(
            EventSubmission.event_id == event.id,
            func.lower(cast(EventSubmission.status, String)) == "approved",
        )
        .order_by(EventSubmission.id.asc())
    )

    submissions = sub_q.scalars().all()

    if not submissions:
        raise HTTPException(
            status_code=400,
            detail="No approved students found for this event.",
        )

    now_utc = datetime.now(timezone.utc)
    now_ist = _now_ist_aware()
    academic_year = _academic_year_from_date(now_ist)

    venue_name = (
        getattr(event, "venue_name", None)
        or getattr(event, "venue", None)
        or getattr(event, "location", None)
        or ""
    ).strip() or "N/A"

    at_q = await db.execute(
        select(ActivityType).where(ActivityType.id.in_(activity_type_ids))
    )
    at_by_id = {int(a.id): a for a in at_q.scalars().all()}

    student_ids = sorted(
        {int(s.student_id) for s in submissions if s.student_id is not None}
    )

    st_q = await db.execute(select(Student).where(Student.id.in_(student_ids)))
    student_by_id = {int(s.id): s for s in st_q.scalars().all()}

    async def _hours_in_window(student_id: int, at_id: int) -> float:
        session_end = func.coalesce(
            ActivitySession.expires_at,
            ActivitySession.submitted_at,
            end_utc,
        )

        hrs_q = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        func.greatest(
                            0.0,
                            func.extract(
                                "epoch",
                                (
                                    func.least(session_end, end_utc)
                                    - func.greatest(ActivitySession.started_at, start_utc)
                                ),
                            ) / 3600.0,
                        )
                    ),
                    0.0,
                )
            ).where(
                ActivitySession.student_id == student_id,
                ActivitySession.activity_type_id == at_id,
                func.lower(cast(ActivitySession.status, String)) == "approved",
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )

        return float(hrs_q.scalar() or 0.0)

    issued = 0
    issued_student_ids: set[int] = set()
    skipped_existing = 0
    skipped_no_hours = 0

    for sub in submissions:
        if issued >= limit:
            break

        if sub.student_id is None:
            continue

        student = student_by_id.get(int(sub.student_id))
        if not student:
            continue

        student_name = (getattr(student, "name", None) or "Student").strip()
        usn = (getattr(student, "usn", None) or "").strip()

        for at_id in activity_type_ids:
            if issued >= limit:
                break

            at_id = int(at_id)

            existing_q = await db.execute(
                select(Certificate.id).where(
                    Certificate.submission_id == sub.id,
                    Certificate.activity_type_id == at_id,
                )
            )

            if existing_q.scalar_one_or_none():
                skipped_existing += 1
                continue

            hours = await _hours_in_window(int(sub.student_id), at_id)

            if hours <= 0:
                skipped_no_hours += 1
                continue

            at = at_by_id.get(at_id)
            activity_type_name = (
                getattr(at, "name", None) or ""
            ).strip() or f"Activity Type #{at_id}"

            points_awarded = await _certificate_points_for_event_activity(
                db=db,
                event_id=event.id,
                at=at,
                at_id=at_id,
                hours=hours,
            )

            cert_no = await _next_certificate_no(db, academic_year, now_ist)

            cert = Certificate(
                certificate_no=cert_no,
                submission_id=sub.id,
                student_id=sub.student_id,
                event_id=event.id,
                activity_type_id=at_id,
                issued_at=now_utc,
            )

            db.add(cert)
            await db.flush()

            sig = sign_cert(cert.certificate_no)
            verify_url = (
                f"{settings.PUBLIC_BASE_URL}/api/public/certificates/verify"
                f"?cert_id={quote(cert.certificate_no)}&sig={quote(sig)}"
            )

            pdf_bytes = build_certificate_pdf(
                template_pdf_path=settings.CERT_TEMPLATE_PDF_PATH,
                certificate_no=cert.certificate_no,
                issue_date=(
                    cert.issued_at.date().isoformat()
                    if cert.issued_at
                    else now_ist.date().isoformat()
                ),
                student_name=student_name,
                usn=usn,
                activity_type=activity_type_name,
                venue_name=venue_name,
                activity_points=int(points_awarded),
                verify_url=verify_url,
            )

            object_key = upload_certificate_pdf_bytes(cert.id, pdf_bytes)
            cert.pdf_path = object_key

            issued += 1
            issued_student_ids.add(int(sub.student_id))

    await db.commit()

    if issued_student_ids:
        try:
            await _send_certificate_ready_notifications(
                db,
                event=event,
                student_ids=sorted(issued_student_ids),
            )
        except Exception as push_error:
            await db.rollback()
            print(
                "[Push Certificate Ready] Failed after certificate commit:",
                {
                    "event_id": event.id,
                    "error": repr(push_error),
                },
            )

    remaining_q = await db.execute(
        select(func.count(EventSubmission.id))
        .where(
            EventSubmission.event_id == event.id,
            func.lower(cast(EventSubmission.status, String)) == "approved",
        )
    )

    approved_total = int(remaining_q.scalar() or 0)

    return {
        "event_id": event_id,
        "batch_limit": limit,
        "certificates_issued_now": issued,
        "approved_submissions_total": approved_total,
        "skipped_existing": skipped_existing,
        "skipped_no_hours": skipped_no_hours,
        "done": issued == 0,
        "message": (
            "Batch generated. Run again until certificates_issued_now becomes 0."
            if issued > 0
            else "No more missing certificates found."
        ),
    }
# =========================================================
# ---------------------- CERT LIST (STUDENT) ----------------
# =========================================================

async def list_student_event_certificates(db: AsyncSession, student_id: int, event_id: int) -> list[dict]:
    q = await db.execute(
        select(Certificate, ActivityType.name)
        .outerjoin(ActivityType, ActivityType.id == Certificate.activity_type_id)
        .where(
            Certificate.student_id == student_id,
            Certificate.event_id == event_id,
            Certificate.revoked_at.is_(None),
        )
        .order_by(Certificate.issued_at.desc(), Certificate.id.desc())
    )

    rows = q.all()
    out = []
    for cert, at_name in rows:
        pdf_url = None
        if cert.pdf_path:
            try:
                pdf_url = presign_certificate_download_url(cert.pdf_path, expires_in=3600)
            except Exception:
                pdf_url = None

        out.append({
            "id": cert.id,
            "certificate_no": cert.certificate_no,
            "issued_at": cert.issued_at,
            "event_id": cert.event_id,
            "submission_id": cert.submission_id,
            "activity_type_id": cert.activity_type_id,
            "activity_type_name": at_name or f"Activity Type #{cert.activity_type_id}",
            "pdf_url": pdf_url,
        })
    return out


async def regenerate_event_certificates(db: AsyncSession, event_id: int):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # delete only certificates belonging to APPROVED submissions of this event
    subq = await db.execute(
        select(EventSubmission.id).where(
            EventSubmission.event_id == event_id,
            func.lower(cast(EventSubmission.status, String)) == "approved",
        )
    )
    sub_ids = [int(x) for x in subq.scalars().all()]

    if not sub_ids:
        raise HTTPException(
            status_code=400,
            detail="No approved students found for this event.",
        )

    await db.execute(
        sql_delete(Certificate).where(Certificate.submission_id.in_(sub_ids))
    )
    await db.commit()

    issued = await _issue_certificates_for_event(db, event)

    if issued == 0:
        raise HTTPException(
            status_code=400,
            detail="No certificates generated. Only approved students are eligible.",
        )

    return {"event_id": event_id, "certificates_issued": issued}


# =========================================================
# ---------------------- THUMBNAIL -------------------------
# =========================================================

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
def _storage_provider() -> str:
    return os.getenv("S3_PROVIDER", "minio").strip().lower()


def _event_thumbnail_bucket() -> str:
    if _storage_provider() == "aws":
        return (
            os.getenv("EVENT_THUMBNAIL_BUCKET")
            or os.getenv("AWS_S3_BUCKET_EVENT_THUMBNAILS")
            or os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS")
            or "vikasana-event-thumbnails-652197206453-ap-south-1-an"
        ).strip()

    return (
        os.getenv("EVENT_THUMBNAIL_BUCKET")
        or os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS")
        or "vikasana-event-thumbnails"
    ).strip()


def _aws_s3_client():
    region = os.getenv("AWS_REGION", "ap-south-1").strip()

    access_key = (
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("MINIO_ACCESS_KEY")
        or os.getenv("MINIO_ROOT_USER")
    )

    secret_key = (
        os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("MINIO_SECRET_KEY")
        or os.getenv("MINIO_ROOT_PASSWORD")
    )

    if not access_key or not secret_key:
        raise HTTPException(status_code=500, detail="AWS S3 credentials missing")

    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )


def _aws_s3_public_style_url(bucket: str, object_name: str) -> str:
    region = os.getenv("AWS_REGION", "ap-south-1").strip()
    return f"https://{bucket}.s3.{region}.amazonaws.com/{object_name}"


async def upload_event_thumbnail_file(
    file: UploadFile,
    admin_id: int,
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    content_type = (file.content_type or "").lower().strip()

    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content_type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 5 MB")

    bucket = _event_thumbnail_bucket()

    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }

    ext = ext_map.get(content_type, "jpg")
    object_name = f"thumbnails/{admin_id}/{uuid.uuid4().hex}.{ext}"

    provider = _storage_provider()

    if provider == "aws":
        s3 = _aws_s3_client()

        # Do NOT create bucket here. Bucket already exists in AWS.
        s3.put_object(
            Bucket=bucket,
            Key=object_name,
            Body=data,
            ContentType=content_type,
        )

        public_url = _aws_s3_public_style_url(bucket, object_name)

    else:
        minio = get_minio()

        ensure_bucket(minio, bucket)

        minio.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

        public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
        if public_base:
            public_url = f"{public_base}/{bucket}/{object_name}"
        else:
            public_url = get_presigned_url(
                bucket=bucket,
                object_name=object_name,
                expiry_seconds=3600,
                public=True,
            )

    return {
        "object_name": object_name,
        "public_url": public_url,
        "content_type": content_type,
        "size": len(data),
        "bucket": bucket,
        "storage_provider": provider,
    }
# =========================================================
# ---------------------- ADMIN -----------------------------
# =========================================================
async def create_event(db: AsyncSession, payload) -> dict:
    """
    ✅ UPDATED create_event:
    - end_time is OPTIONAL
    - if end_time missing → window logic treats it as next-day / 24h window
    - validates ActivityType IDs exist
    - inserts Event + mappings atomically with ONE commit
    - supports scoring_rules for AUTO / MANUAL
    - clears Redis cache for admin/student event lists
    """

    # ─────────────────────────────────────────────
    # Parse date/time
    # ─────────────────────────────────────────────
    event_date: date_type | None = _parse_date(
        getattr(payload, "event_date", None) or getattr(payload, "date", None)
    )
    if not event_date:
        raise HTTPException(status_code=422, detail="event_date is required")

    start_time: time_type | None = _parse_time(
        getattr(payload, "start_time", None) or getattr(payload, "time", None)
    )
    if start_time is None:
        raise HTTPException(status_code=422, detail="start_time is required (HH:MM)")

    # ✅ end_time OPTIONAL
    end_time: time_type | None = _parse_time(getattr(payload, "end_time", None))

    # ✅ if end_time missing → store same as start_time
    # runtime window logic will treat it as next-day / 24h-style window
    if end_time is None:
        end_time = start_time

    # If admin explicitly provided end_time, validate it
    if getattr(payload, "end_time", None) is not None and end_time <= start_time:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")

    # ─────────────────────────────────────────────
    # required_photos safety
    # ─────────────────────────────────────────────
    required_photos = int(getattr(payload, "required_photos", 3) or 3)
    if required_photos < 3 or required_photos > 5:
        raise HTTPException(status_code=422, detail="required_photos must be between 3 and 5")

    # ─────────────────────────────────────────────
    # Activity type ids
    # ─────────────────────────────────────────────
    ids: List[int] = list(getattr(payload, "activity_type_ids", None) or [])
    ids = sorted({int(x) for x in ids if x is not None and int(x) > 0})
    if not ids:
        raise HTTPException(status_code=422, detail="Please select at least 1 activity type")

    q = await db.execute(select(ActivityType.id).where(ActivityType.id.in_(ids)))
    existing = {int(r[0]) for r in q.all()}
    missing = [i for i in ids if i not in existing]
    if missing:
        raise HTTPException(status_code=422, detail=f"Invalid activity_type_ids: {missing}")

    maps_url = getattr(payload, "maps_url", None) or getattr(payload, "venue_maps_url", None)

    # ─────────────────────────────────────────────
    # scoring_rules map
    # ─────────────────────────────────────────────
    scoring_rules = getattr(payload, "scoring_rules", None) or []
    rule_map = {}

    for r in scoring_rules:
        at_id = getattr(r, "activity_type_id", None)
        if at_id is None and isinstance(r, dict):
            at_id = r.get("activity_type_id")
        if at_id is None:
            continue
        rule_map[int(at_id)] = r

    def _get(rule_obj, key, default=None):
        if rule_obj is None:
            return default
        if isinstance(rule_obj, dict):
            return rule_obj.get(key, default)
        return getattr(rule_obj, key, default)

    # ─────────────────────────────────────────────
    # Create event + mappings
    # ─────────────────────────────────────────────
    try:
        event = Event(
            title=str(getattr(payload, "title", "")).strip(),
        description=(getattr(payload, "description", None) or None),
        required_photos=required_photos,
        photo_capture_mode=(
            getattr(payload, "photo_capture_mode", None) or "normal"
        ),
        is_active=True,
        event_date=event_date,
        start_time=start_time,
         end_time=end_time,
        thumbnail_url=getattr(payload, "thumbnail_url", None),
        venue_name=getattr(payload, "venue_name", None),
        maps_url=maps_url,
        location_lat=getattr(payload, "location_lat", None),
        location_lng=getattr(payload, "location_lng", None),
        geo_radius_m=getattr(payload, "geo_radius_m", None),

    # ✅ NEW: participant/volunteer role protection
        exclusive_group_key=getattr(payload, "exclusive_group_key", None),
        event_role=(getattr(payload, "event_role", None) or "PARTICIPANT").upper(),
    )
        db.add(event)
        await db.flush()

        rows = []
        for at_id in ids:
            rule = rule_map.get(int(at_id))
            rows.append(
                EventActivityType(
                    event_id=event.id,
                    activity_type_id=int(at_id),
                    score_mode=str(_get(rule, "score_mode", "AUTO") or "AUTO").upper(),
                    manual_points=_get(rule, "manual_points", None),
                    min_required_hours=_get(rule, "min_required_hours", None),
                )
            )

        db.add_all(rows)

        await db.commit()
        await db.refresh(event)

        # ✅ clear cached event lists
        await cache_delete_pattern("admin:events:*")
        await cache_delete_pattern("student:events:*")

        # --------------------------------------------------
        # PUSH NOTIFICATION: NEW EVENT
        # --------------------------------------------------
        #
        # The event is already committed above.
        # Push delivery must never make event creation fail.
        #
        try:
            venue_name = (
                str(getattr(event, "venue_name", "") or "").strip()
            )

            notification_body = (
                f"{event.title} is now available"
            )

            if venue_name:
                notification_body += f" at {venue_name}."

            else:
                notification_body += "."

            # Only evaluate students who can actually receive a push.
            # Final visibility is determined by the SAME rule used by
            # GET /student/events.
            candidate_result = await db.execute(
                select(Student.id)
                .join(
                    StudentPushDevice,
                    StudentPushDevice.student_id == Student.id,
                )
                .where(
                    Student.is_active.is_(True),
                    StudentPushDevice.is_active.is_(True),
                )
                .distinct()
            )

            candidate_student_ids = list(
                candidate_result.scalars().all()
            )

            visible_student_ids: list[int] = []

            for student_id in candidate_student_ids:
                if await can_student_view_event(
                    db=db,
                    student_id=student_id,
                    event=event,
                ):
                    visible_student_ids.append(student_id)

            push_result = await send_student_push_notifications(
                db,
                title="New Event Published 🎉",
                body=notification_body,
                data={
                    "type": "event_created",
                    "event_id": event.id,
                    "event_title": event.title,
                    "route": "/(student)/dashboard",
                },
                student_ids=visible_student_ids,
            )

            print(
                "[Push] Event creation notification:",
                {
                    "event_id": event.id,
                    "result": push_result,
                },
            )

        except Exception as push_error:
            print(
                "[Push] Event notification failed after event commit:",
                repr(push_error),
            )

        return {
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "required_photos": event.required_photos,
            "is_active": event.is_active,
            "event_date": event.event_date,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "thumbnail_url": getattr(event, "thumbnail_url", None),
            "venue_name": getattr(event, "venue_name", None),
            "maps_url": getattr(event, "maps_url", None),
            "location_lat": getattr(event, "location_lat", None),
            "location_lng": getattr(event, "location_lng", None),
            "geo_radius_m": getattr(event, "geo_radius_m", None),
            "exclusive_group_key": getattr(event, "exclusive_group_key", None),
            "event_role": getattr(event, "event_role", None) or "PARTICIPANT",
            "activity_type_ids": ids,
            "scoring_rules": [
                {
                    "activity_type_id": row.activity_type_id,
                    "score_mode": row.score_mode,
                    "manual_points": row.manual_points,
                    "min_required_hours": row.min_required_hours,
                }
                for row in rows
            ],
        }

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")
# =========================================================
# ---------------------- ADMIN: UPDATE EVENT --------------
# =========================================================
async def update_event(db: AsyncSession, event_id: int, payload) -> dict:
    """
    ✅ Update event + (optionally) replace Event ↔ ActivityType mappings.

    Partial update:
      - only fields present in payload are applied
      - if activity_type_ids is provided, mappings are replaced

    Validations:
      - required_photos in [3..5] if provided
      - end_time > start_time (same day, only if explicitly provided)
      - activity_type_ids must exist if provided

    Also:
      - supports scoring_rules for AUTO / MANUAL
      - clears Redis cache for admin/student event lists after successful update
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    title = getattr(payload, "title", None)
    description = getattr(payload, "description", None)
    thumbnail_url = getattr(payload, "thumbnail_url", None)
    venue_name = getattr(payload, "venue_name", None)
    maps_url = getattr(payload, "maps_url", None) or getattr(payload, "venue_maps_url", None)

    location_lat = getattr(payload, "location_lat", None)
    location_lng = getattr(payload, "location_lng", None)
    geo_radius_m = getattr(payload, "geo_radius_m", None)
    exclusive_group_key = getattr(payload, "exclusive_group_key", None)
    event_role = getattr(payload, "event_role", None)

    is_active = getattr(payload, "is_active", None)

    new_event_date = _parse_date(getattr(payload, "event_date", None) or getattr(payload, "date", None))
    new_start_time = _parse_time(getattr(payload, "start_time", None) or getattr(payload, "time", None))
    new_end_time = _parse_time(getattr(payload, "end_time", None))

    required_photos_in = getattr(payload, "required_photos", None)
    photo_capture_mode_in = getattr(payload, "photo_capture_mode", None)

    activity_type_ids_raw = getattr(payload, "activity_type_ids", None)
    replace_mappings = activity_type_ids_raw is not None

    if title is not None:
        event.title = str(title).strip()

    if description is not None:
        event.description = description or None

    if thumbnail_url is not None:
        event.thumbnail_url = thumbnail_url or None

    if venue_name is not None:
        event.venue_name = venue_name or None

    if maps_url is not None:
        event.maps_url = maps_url or None

    if location_lat is not None:
        event.location_lat = location_lat

    if location_lng is not None:
        event.location_lng = location_lng

    if geo_radius_m is not None:
        event.geo_radius_m = geo_radius_m

    if exclusive_group_key is not None:
        event.exclusive_group_key = exclusive_group_key or None

    if event_role is not None:
        event.event_role = str(event_role or "PARTICIPANT").strip().upper()

    if is_active is not None:
        event.is_active = bool(is_active)

    if required_photos_in is not None:
        rp = int(required_photos_in)
        if rp < 3 or rp > 5:
            raise HTTPException(status_code=422, detail="required_photos must be between 3 and 5")
        event.required_photos = rp

    if photo_capture_mode_in is not None:
        event.photo_capture_mode = str(photo_capture_mode_in).strip().lower()

    if new_event_date is not None:
        event.event_date = new_event_date
    if new_start_time is not None:
        event.start_time = new_start_time
    if new_end_time is not None:
        event.end_time = new_end_time

    if (new_event_date is not None) or (new_start_time is not None) or (new_end_time is not None):
        if not event.event_date:
            raise HTTPException(status_code=422, detail="event_date is required")
        if not event.start_time:
            raise HTTPException(status_code=422, detail="start_time is required")
        if not event.end_time:
            raise HTTPException(status_code=422, detail="end_time is required")

        st = event.start_time if isinstance(event.start_time, time_type) else _parse_time(event.start_time)
        et = event.end_time if isinstance(event.end_time, time_type) else _parse_time(event.end_time)
        if st is None or et is None:
            raise HTTPException(status_code=422, detail="Invalid start_time/end_time")

        if getattr(payload, "end_time", None) is not None and et <= st:
            raise HTTPException(status_code=422, detail="end_time must be after start_time")

    new_ids: list[int] = []
    rows = []

    if replace_mappings:
        new_ids = sorted({int(x) for x in (activity_type_ids_raw or []) if x is not None and int(x) > 0})
        if not new_ids:
            raise HTTPException(status_code=422, detail="Please select at least 1 activity type")

        q = await db.execute(select(ActivityType.id).where(ActivityType.id.in_(new_ids)))
        existing = {int(r[0]) for r in q.all()}
        missing = [i for i in new_ids if i not in existing]
        if missing:
            raise HTTPException(status_code=422, detail=f"Invalid activity_type_ids: {missing}")

        scoring_rules = getattr(payload, "scoring_rules", None) or []
        rule_map = {}

        for r in scoring_rules:
            at_id = getattr(r, "activity_type_id", None)
            if at_id is None and isinstance(r, dict):
                at_id = r.get("activity_type_id")
            if at_id is None:
                continue
            rule_map[int(at_id)] = r

        def _get(rule_obj, key, default=None):
            if rule_obj is None:
                return default
            if isinstance(rule_obj, dict):
                return rule_obj.get(key, default)
            return getattr(rule_obj, key, default)

        try:
            await db.execute(sql_delete(EventActivityType).where(EventActivityType.event_id == event_id))

            for at_id in new_ids:
                rule = rule_map.get(int(at_id))
                rows.append(
                    EventActivityType(
                        event_id=event_id,
                        activity_type_id=int(at_id),
                        score_mode=str(_get(rule, "score_mode", "AUTO") or "AUTO").upper(),
                        manual_points=_get(rule, "manual_points", None),
                        min_required_hours=_get(rule, "min_required_hours", None),
                    )
                )

            db.add_all(rows)

        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update activity mappings: {str(e)}")

    try:
        await db.commit()
        await db.refresh(event)

        # ✅ clear cached event lists
        await cache_delete_pattern("admin:events:*")
        await cache_delete_pattern("student:events:*")

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update event: {str(e)}")

    if not replace_mappings:
        mapped_q = await db.execute(
            select(EventActivityType).where(EventActivityType.event_id == event_id)
        )
        mapping_rows = mapped_q.scalars().all()
        new_ids = sorted(
            {
                int(x.activity_type_id)
                for x in mapping_rows
                if getattr(x, "activity_type_id", None) is not None
            }
        )
    else:
        mapping_rows = rows

    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "required_photos": event.required_photos,
        "is_active": event.is_active,
        "event_date": event.event_date,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "thumbnail_url": getattr(event, "thumbnail_url", None),
        "venue_name": getattr(event, "venue_name", None),
        "maps_url": getattr(event, "maps_url", None),
        "location_lat": getattr(event, "location_lat", None),
        "location_lng": getattr(event, "location_lng", None),
        "geo_radius_m": getattr(event, "geo_radius_m", None),
        "exclusive_group_key": getattr(event, "exclusive_group_key", None),
        "event_role": getattr(event, "event_role", None) or "PARTICIPANT",
        "activity_type_ids": new_ids,
        "scoring_rules": [
            {
                "activity_type_id": row.activity_type_id,
                "score_mode": row.score_mode,
                "manual_points": row.manual_points,
                "min_required_hours": row.min_required_hours,
            }
            for row in mapping_rows
        ],
    }
    

async def _finalize_unfinished_event_submissions(
    db: AsyncSession,
    event_id: int,
    submitted_at: datetime | None = None,
) -> dict:
    """
    Finalize unfinished submissions when an event ends.

    Rules:
    - 0 photos:
        -> EXPIRED

    - >= 1 photo:
        -> FLAGGED
        -> submitted_at is recorded
        -> points stay uncredited
        -> admin must approve/reject

    This intentionally does NOT calculate or credit points here.

    On admin approval, existing scoring logic uses:
        first captured photo -> last captured photo

    for AUTO scoring, while MANUAL scoring uses the configured
    event points only after approval.
    """

    finalized_at = submitted_at or datetime.now(timezone.utc)

    unfinished_result = await db.execute(
        select(EventSubmission)
        .options(
            selectinload(EventSubmission.photos),
        )
        .where(
            EventSubmission.event_id == event_id,
            EventSubmission.status.in_(
                ["in_progress", "draft"]
            ),
        )
    )

    unfinished_submissions = (
        unfinished_result.scalars().unique().all()
    )

    flagged_count = 0
    expired_count = 0

    for submission in unfinished_submissions:
        photos = list(
            getattr(submission, "photos", None) or []
        )

        # No evidence was captured.
        if len(photos) == 0:
            submission.status = "expired"
            expired_count += 1
            continue

        # Evidence exists, but the student never manually submitted.
        # Auto-submit it for admin review.
        submission.status = "flagged"
        submission.submitted_at = finalized_at

        # Never award points during automatic finalization.
        submission.points_credited = False
        submission.awarded_points = 0

        # FLAGGED is a review state, not a rejection.
        submission.rejection_reason = None

        flagged_count += 1

    return {
        "unfinished_found": len(unfinished_submissions),
        "flagged": flagged_count,
        "expired": expired_count,
    }


async def end_event(db: AsyncSession, event_id: int):
    """
    Manually end an event immediately.

    Uses the same unfinished-submission finalization rules
    as the scheduled automatic event-end worker.
    """

    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        )

    # End immediately.
    event.is_active = False

    now_ist = _now_ist_aware()
    now_utc = datetime.now(timezone.utc)

    # Preserve existing manual-end behaviour:
    # event end_time becomes the moment Admin ended it.
    event.end_time = (
        now_ist.time().replace(tzinfo=None)
    )

    await _finalize_unfinished_event_submissions(
        db=db,
        event_id=event_id,
        submitted_at=now_utc,
    )

    await db.commit()
    await db.refresh(event)

    await cache_delete_pattern("admin:events:*")
    await cache_delete_pattern("student:events:*")

    return event


async def auto_finalize_ended_events(
    db: AsyncSession,
) -> dict:
    """
    Find active events whose configured event window has ended
    and automatically finalize their unfinished submissions.

    Intended to be called periodically by Celery Beat.
    """

    now_utc = datetime.now(timezone.utc)

    summary = {
        "checked_at": now_utc.isoformat(),
        "events_checked": 0,
        "events_finalized": 0,
        "submissions_flagged": 0,
        "submissions_expired": 0,
        "errors": [],
    }

    result = await db.execute(
        select(Event)
        .where(
            Event.is_active.is_(True),
            Event.event_date.is_not(None),
            Event.start_time.is_not(None),
        )
        .order_by(
            Event.event_date.asc(),
            Event.start_time.asc(),
        )
    )

    events = list(result.scalars().all())

    summary["events_checked"] = len(events)

    changed = False

    for event in events:
        try:
            _, event_end_utc = _event_window_utc(event)
        except Exception as exc:
            summary["errors"].append(
                {
                    "event_id": event.id,
                    "error": (
                        "invalid_event_window: "
                        f"{str(exc)}"
                    ),
                }
            )
            continue

        # Event is still running.
        if now_utc < event_end_utc:
            continue

        counts = (
            await _finalize_unfinished_event_submissions(
                db=db,
                event_id=event.id,
                # Record the configured event-end instant,
                # not the Celery worker's few-minutes-later run time.
                submitted_at=event_end_utc,
            )
        )

        event.is_active = False

        summary["events_finalized"] += 1
        summary["submissions_flagged"] += int(
            counts["flagged"]
        )
        summary["submissions_expired"] += int(
            counts["expired"]
        )

        changed = True

    if changed:
        await db.commit()

        await cache_delete_pattern(
            "admin:events:*"
        )
        await cache_delete_pattern(
            "student:events:*"
        )

    return summary


async def delete_event(db: AsyncSession, event_id: int) -> None:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()

    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    # Imported participant history is permanent event history.
    #
    # Stop before deleting certificates, photos or submissions.
    # The database also uses ON DELETE RESTRICT as a final safeguard.
    participant_history_result = await db.execute(
        select(EventParticipant.id)
        .where(EventParticipant.event_id == event_id)
        .limit(1)
    )

    import_batch_result = await db.execute(
        select(EventParticipantImportBatch.id)
        .where(EventParticipantImportBatch.event_id == event_id)
        .limit(1)
    )

    if (
        participant_history_result.scalar_one_or_none() is not None
        or import_batch_result.scalar_one_or_none() is not None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Event cannot be permanently deleted because "
                "participant import/history records exist. "
                "End or deactivate the event instead."
            ),
        )

    try:
        # 1. Get all submissions for this event
        sub_result = await db.execute(
            select(EventSubmission.id).where(EventSubmission.event_id == event_id)
        )
        submission_ids = [int(row[0]) for row in sub_result.fetchall() if row[0] is not None]

        if submission_ids:
            # 2. Delete certificates first
            # certificates.submission_id -> event_submissions.id
            await db.execute(
                sql_delete(Certificate).where(
                    Certificate.submission_id.in_(submission_ids)
                )
            )

            # 3. Delete event submission photos
            await db.execute(
                sql_delete(EventSubmissionPhoto).where(
                    EventSubmissionPhoto.submission_id.in_(submission_ids)
                )
            )

            # 4. Delete event submissions
            await db.execute(
                sql_delete(EventSubmission).where(
                    EventSubmission.event_id == event_id
                )
            )

        # 5. Delete event activity type mappings
        await db.execute(
            sql_delete(EventActivityType).where(
                EventActivityType.event_id == event_id
            )
        )

        # 6. Delete event
        await db.execute(
            sql_delete(Event).where(Event.id == event_id)
        )

        await db.commit()

        # 7. Clear cache after successful delete
        await cache_delete_pattern("admin:events:*")
        await cache_delete_pattern("student:events:*")

    except HTTPException:
        await db.rollback()
        raise

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete event: {str(e)}"
        )

async def list_event_submissions(db: AsyncSession, event_id: int):
    q = await db.execute(
        select(EventSubmission)
        .options(
            selectinload(EventSubmission.photos),
        )
        .where(EventSubmission.event_id == event_id)
        .order_by(EventSubmission.id.desc())
    )
    return q.scalars().all()

async def approve_submission(db: AsyncSession, submission_id: int):
    q = await db.execute(
        select(EventSubmission)
        .options(selectinload(EventSubmission.photos))
        .where(EventSubmission.id == submission_id)
    )
    submission = q.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status not in ("submitted", "flagged"):
        raise HTTPException(
            status_code=400,
            detail="Only submitted or flagged items can be approved",
        )

    event = await db.get(Event, submission.event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # approve submission
    submission.status = "approved"
    if hasattr(submission, "approved_at"):
        submission.approved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(submission)

    # create/update approved activity sessions
    sessions = await create_or_update_activity_session_from_submission(
        db=db,
        submission=submission,
        event=event,
        target_status=ActivitySessionStatus.APPROVED,
    )

    # copy event photos into activity session photos
    for session in sessions:
        await copy_event_photos_to_activity_session(db, submission, session)

    # create/update face check rows
    for session in sessions:
        await create_face_check_for_activity_session(db, submission, session)

    # ✅ CREDIT POINTS TO STUDENT TOTAL ONLY ONCE
    await _credit_submission_points_once(db, submission, event)

    # generate certificates
    

    # reload latest submission
    q = await db.execute(
        select(EventSubmission)
        .options(selectinload(EventSubmission.photos))
        .where(EventSubmission.id == submission_id)
    )
    submission = q.scalar_one()

    return submission


async def reject_submission(db: AsyncSession, submission_id: int, reason: str):
    q = await db.execute(
        select(EventSubmission)
        .options(selectinload(EventSubmission.photos))
        .where(EventSubmission.id == submission_id)
    )
    submission = q.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status not in ("submitted", "flagged"):
        raise HTTPException(
            status_code=400,
            detail="Only submitted or flagged items can be rejected",
        )

    submission.status = "rejected"
    if hasattr(submission, "rejection_reason"):
        submission.rejection_reason = reason

    await db.commit()

    # reload with photos eagerly loaded to avoid MissingGreenlet during response serialization
    q = await db.execute(
        select(EventSubmission)
        .options(selectinload(EventSubmission.photos))
        .where(EventSubmission.id == submission_id)
    )
    submission = q.scalar_one()

    return submission


# =========================================================
# ---------------------- STUDENT ---------------------------
# =========================================================
async def list_active_events(db: AsyncSession) -> list[Event]:
    """
    Returns Event ORM objects with activity_types eager-loaded
    so _event_out_dict(ev) can build scoring_rules (points) safely.
    """
    q = await db.execute(
        select(Event)
        .options(selectinload(Event.activity_types))   # ✅ loads mapping rows for points
        .where(Event.event_date.isnot(None))
        .order_by(
            Event.event_date.desc(),
            Event.start_time.asc().nulls_last(),
            Event.id.desc(),
        )
    )
    return q.scalars().all()

async def register_for_event(db: AsyncSession, student_id: int, event_id: int):
    q = await db.execute(select(Event).where(Event.id == event_id))
    event = q.scalar_one_or_none()

    if not event or not getattr(event, "is_active", True):
        raise HTTPException(status_code=404, detail="Event not found")

    start_ist, end_ist = _event_window_ist_aware(event)
    now_ist = _now_ist_aware()

    if now_ist > end_ist:
        raise HTTPException(
            status_code=403,
            detail="Event has ended.",
        )

    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.event_id == event_id,
            EventSubmission.student_id == student_id,
        )
    )
    existing = q.scalar_one_or_none()

    # -----------------------------------------------------
    # EXISTING REGISTRATION
    # -----------------------------------------------------
    if existing:
        # Student registered earlier while event was upcoming.
        # Once the event is live, the same endpoint promotes
        # registration into actual participation.
        if (
            existing.status == "registered"
            and start_ist <= now_ist <= end_ist
        ):
            existing.status = "in_progress"

            await db.commit()
            await db.refresh(existing)

            return {
                "submission_id": existing.id,
                "status": existing.status,
                "created": False,
                "started": True,
            }

        return {
            "submission_id": existing.id,
            "status": existing.status,
            "created": False,
            "started": False,
        }

    # -----------------------------------------------------
    # NEW REGISTRATION
    # -----------------------------------------------------
    # Registering never starts participation automatically.
    # This applies both before the event and while it is live.
    #
    # A second request from the Start Event action, while the
    # event is ongoing, promotes "registered" -> "in_progress".
    submission = EventSubmission(
        event_id=event_id,
        student_id=student_id,
        status="registered",
    )

    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    return {
        "submission_id": submission.id,
        "status": submission.status,
        "created": True,
        "started": False,
    }


async def add_photo(
    db: AsyncSession,
    submission_id: int,
    student_id: int,
    seq_no: int,
    image_url: str,
):
    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.id == submission_id,
            EventSubmission.student_id == student_id,
        )
    )
    submission = q.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status != "in_progress":
        raise HTTPException(status_code=400, detail="Submission already completed")

    evq = await db.execute(select(Event).where(Event.id == submission.event_id))
    event = evq.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    _ensure_event_window(event)

    required_photos = int(getattr(event, "required_photos", 3) or 3)
    if seq_no < 1 or seq_no > required_photos:
        raise HTTPException(status_code=400, detail=f"seq_no must be between 1 and {required_photos}")

    q = await db.execute(
        select(EventSubmissionPhoto).where(
            EventSubmissionPhoto.submission_id == submission_id,
            EventSubmissionPhoto.seq_no == seq_no,
        )
    )
    existing_photo = q.scalar_one_or_none()

    if existing_photo:
        existing_photo.image_url = image_url
        await db.commit()
        await db.refresh(existing_photo)
        return existing_photo

    photo = EventSubmissionPhoto(
        submission_id=submission_id,
        seq_no=seq_no,
        image_url=image_url,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    return photo


async def _trigger_face_verification_for_submission(submission_id: int) -> dict:
    """
    Calls the face verification endpoint internally after submission
    to run the actual OpenCV face matching pipeline.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"http://localhost:8000/api/face/verify-event-submission/{submission_id}"
            )
            if resp.status_code == 200:
                return resp.json()
            print(f"[face-verify] HTTP {resp.status_code}: {resp.text}")
            return {"matched": False, "reason": f"HTTP {resp.status_code}"}
    except Exception as e:
        print(f"[face-verify] Error for submission {submission_id}: {e}")
        return {"matched": False, "reason": str(e)}

async def final_submit(db: AsyncSession, submission_id: int, student_id: int, description: str):
    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.id == submission_id,
            EventSubmission.student_id == student_id,
        )
    )
    submission = q.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status != "in_progress":
        raise HTTPException(status_code=400, detail="Already submitted")

    evq = await db.execute(select(Event).where(Event.id == submission.event_id))
    event = evq.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    _ensure_event_window(event)

    required_photos = int(getattr(event, "required_photos", 3) or 3)

    q = await db.execute(
        select(func.count(EventSubmissionPhoto.id)).where(
            EventSubmissionPhoto.submission_id == submission_id
        )
    )
    uploaded_photos = int(q.scalar() or 0)

    if uploaded_photos < required_photos:
        raise HTTPException(
            status_code=400,
            detail=(
                f"You must upload at least {required_photos} photos before submitting. "
                f"Currently uploaded: {uploaded_photos}"
            ),
        )

    now_utc = datetime.now(timezone.utc)

    # Step 1: mark as submitted
    submission.status = "submitted"
    submission.description = description
    if hasattr(submission, "submitted_at"):
        submission.submitted_at = now_utc

    await db.commit()
    await db.refresh(submission)

    # Step 2: create activity session in submitted state
    sessions = await create_or_update_activity_session_from_submission(
        db=db,
        submission=submission,
        event=event,
        target_status=ActivitySessionStatus.SUBMITTED,
    )

    # Step 3: copy photos to activity session
    for session in sessions:
        await copy_event_photos_to_activity_session(db, submission, session)

    # Step 4: create placeholder face rows
    for session in sessions:
        await create_face_check_for_activity_session(db, submission, session)

    # Step 5: run actual face verification
    print(f"[face-verify] Running face verification for submission {submission_id}...")
    face_result = await _trigger_face_verification_for_submission(submission_id)
    any_matched = bool(face_result.get("matched", False))
    print(f"[face-verify] Result: matched={any_matched}, reason={face_result.get('reason')}")

    # Step 6: update ActivityFaceCheck rows with actual result
    processed_object = face_result.get("processed_object")
    for session in sessions:
        photo_res = await db.execute(
            select(ActivityPhoto)
            .where(ActivityPhoto.session_id == session.id)
            .order_by(ActivityPhoto.seq_no.asc())
        )
        chosen_photo = photo_res.scalars().first()
        if not chosen_photo:
            continue

        fc_res = await db.execute(
            select(ActivityFaceCheck)
            .where(
                ActivityFaceCheck.session_id == session.id,
                ActivityFaceCheck.photo_id == chosen_photo.id,
            )
            .order_by(ActivityFaceCheck.id.desc())
        )
        face_check = fc_res.scalar_one_or_none()

        if face_check:
            face_check.matched = any_matched
            face_check.cosine_score = face_result.get("cosine_score")
            face_check.l2_score = face_result.get("l2_score")
            face_check.total_faces = face_result.get("total_faces")
            face_check.processed_object = processed_object
            face_check.reason = face_result.get("reason")
        else:
            db.add(
                ActivityFaceCheck(
                    student_id=student_id,
                    session_id=session.id,
                    photo_id=chosen_photo.id,
                    raw_image_url=chosen_photo.image_url,
                    matched=any_matched,
                    cosine_score=face_result.get("cosine_score"),
                    l2_score=face_result.get("l2_score"),
                    total_faces=face_result.get("total_faces"),
                    processed_object=processed_object,
                    reason=face_result.get("reason"),
                )
            )

    await db.commit()

    # Step 7: geofence check + face check together
    photo_geo_res = await db.execute(
        select(EventSubmissionPhoto).where(
            EventSubmissionPhoto.submission_id == submission_id
        )
    )
    uploaded_photo_rows = photo_geo_res.scalars().all()

    all_in_geofence = (
        len(uploaded_photo_rows) >= required_photos
        and all(bool(getattr(p, "is_in_geofence", False)) for p in uploaded_photo_rows)
    )

    geo_reasons = []
    for p in uploaded_photo_rows:
        if getattr(p, "is_in_geofence", None) is False:
            dist = getattr(p, "distance_m", None)
            if dist is not None:
                geo_reasons.append(f"photo_{p.seq_no}_outside_{int(dist)}m")
            else:
                geo_reasons.append(f"photo_{p.seq_no}_outside_geofence")
        elif getattr(p, "is_in_geofence", None) is None:
            geo_reasons.append(f"photo_{p.seq_no}_gps_missing")

    # Step 8: auto approve only if face matched AND all uploaded photos are inside geofence
    if any_matched and all_in_geofence:
        submission.status = "approved"
        if hasattr(submission, "approved_at"):
            submission.approved_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(submission)

        sessions = await create_or_update_activity_session_from_submission(
            db=db,
            submission=submission,
            event=event,
            target_status=ActivitySessionStatus.APPROVED,
        )

        for session in sessions:
            await copy_event_photos_to_activity_session(db, submission, session)

        for session in sessions:
            await create_face_check_for_activity_session(db, submission, session)

        # ✅ IMPORTANT: credit points to student total only once
        await _credit_submission_points_once(db, submission, event)

        # generate certificates
        

    else:
        # Keep in admin review queue
        submission.status = "submitted"

        reasons = []
        if not any_matched:
            reasons.append("face_mismatch")
        if not all_in_geofence:
            reasons.append("outside_geofence")

        if hasattr(submission, "rejection_reason"):
            combined = reasons + geo_reasons
            submission.rejection_reason = ",".join(combined) if combined else None

        await db.commit()

    await db.refresh(submission)
    return submission
