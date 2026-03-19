# app/controllers/events_controller.py
from __future__ import annotations
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date as date_type, time as time_type, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional
import secrets
from urllib.parse import quote
from app.features.activities.models import ActivityFaceCheck
from sqlalchemy import delete
from typing import List
from fastapi import HTTPException
from sqlalchemy import select, func, delete as sql_delete, update, cast, String
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

import io
import os
import uuid
from fastapi import UploadFile
from app.core.minio_client import get_minio, ensure_bucket

from app.features.events.models import Event, EventSubmission, EventSubmissionPhoto
from app.features.students.models import Student

# ✅ Activity tracking
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType

# ✅ Event ↔ ActivityType mapping
from app.features.events.models import EventActivityType

# ✅ Certificates
from app.features.certificates.models import Certificate, CertificateCounter


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
IST = ZoneInfo("Asia/Kolkata")


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
        # take first 10 chars for YYYY-MM-DD
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
            already.captured_at = getattr(submission, "submitted_at", None) or datetime.now(timezone.utc)
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
                    captured_at=getattr(submission, "submitted_at", None) or datetime.now(timezone.utc),
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

async def create_or_update_activity_session_from_submission(
    db: AsyncSession,
    submission: EventSubmission,
    event: Event,
    target_status: ActivitySessionStatus,
):
    activity_type_ids = await _get_event_activity_type_ids(db, event.id)
    if not activity_type_ids:
        return []

    start_utc, end_utc = _event_window_utc(event)
    now_utc = datetime.now(timezone.utc)
    sessions = []

    for at_id in activity_type_ids:
        q = await db.execute(
            select(ActivitySession).where(
                ActivitySession.student_id == submission.student_id,
                ActivitySession.activity_type_id == at_id,
                ActivitySession.started_at <= end_utc,
                func.coalesce(ActivitySession.submitted_at, ActivitySession.expires_at, end_utc) >= start_utc,
            )
        )
        session = q.scalar_one_or_none()

        if session:
            session.status = target_status

            if target_status in [ActivitySessionStatus.SUBMITTED, ActivitySessionStatus.APPROVED]:
                if session.submitted_at is None:
                    session.submitted_at = getattr(submission, "submitted_at", None) or now_utc

            if session.duration_hours is None:
                session.duration_hours = max(
                    0.0, (end_utc - start_utc).total_seconds() / 3600.0
                )
        else:
            session = ActivitySession(
                student_id=submission.student_id,
                activity_type_id=at_id,
                activity_name=getattr(event, "title", "Event Activity"),
                description=getattr(submission, "description", None),
                session_code=secrets.token_hex(8),
                started_at=start_utc,
                expires_at=end_utc,
                submitted_at=(
                    getattr(submission, "submitted_at", None) or now_utc
                    if target_status in [ActivitySessionStatus.SUBMITTED, ActivitySessionStatus.APPROVED]
                    else None
                ),
                status=target_status,
                duration_hours=max(
                    0.0, (end_utc - start_utc).total_seconds() / 3600.0
                ),
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
) -> int:
    activity_type_ids = await _get_event_activity_type_ids(db, event.id)
    if not activity_type_ids:
        return 0

    start_utc, end_utc = _event_window_utc(event)
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    total_points = 0

    at_q = await db.execute(
        select(ActivityType).where(ActivityType.id.in_(activity_type_ids))
    )
    activity_types = {int(a.id): a for a in at_q.scalars().all()}

    for at_id in activity_type_ids:
        session_end = func.coalesce(
            ActivitySession.submitted_at,
            ActivitySession.expires_at,
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
                ActivitySession.student_id == submission.student_id,
                ActivitySession.activity_type_id == at_id,
                func.lower(cast(ActivitySession.status, String)) == "approved",
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )

        hours = float(hrs_q.scalar() or 0.0)
        if hours <= 0:
            continue

        at = activity_types.get(int(at_id))
        if not at:
            continue

        ppu = getattr(at, "points_per_unit", None)
        hpu = getattr(at, "hours_per_unit", None)
        max_points = getattr(at, "max_points", None)

        points_awarded = 0
        if ppu is not None and hpu:
            try:
                points_awarded = int(round((hours / float(hpu)) * float(ppu)))
            except Exception:
                points_awarded = 0

        if max_points is not None:
            try:
                points_awarded = min(points_awarded, int(max_points))
            except Exception:
                pass

        total_points += max(0, int(points_awarded))

    return total_points


async def _credit_submission_points_once(
    db: AsyncSession,
    submission: EventSubmission,
    event: Event,
) -> int:
    if bool(getattr(submission, "points_credited", False)):
        return int(getattr(submission, "awarded_points", 0) or 0)

    total_points = await _calculate_submission_points(db, submission, event)

    student = await db.get(Student, submission.student_id)
    if not student:
        return 0

    student.total_points_earned = int(student.total_points_earned or 0) + int(total_points)
    submission.awarded_points = int(total_points)
    submission.points_credited = True

    if getattr(submission, "approved_at", None) is None:
        submission.approved_at = datetime.now(timezone.utc)

    await db.commit()
    return total_points


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
        ActivitySession.submitted_at,
        ActivitySession.expires_at,
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
    - Generates certificates for them

    ✅ FIXES:
    - ActivitySession.status matched case-insensitively (handles DB values like "APPROVED")
    - Fallback activity-type inference uses OVERLAP logic (not started_at-only)
    - Uses session_end = coalesce(submitted_at, expires_at, end_utc) to avoid NULL-end killing matches
    - Treats expired/pending submissions as re-approvable if student is eligible
    - Uses consistent UTC window logic
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
            ActivitySession.submitted_at,
            ActivitySession.expires_at,
            end_utc,  # ✅ critical fallback
        )

        aq = await db.execute(
            select(func.distinct(ActivitySession.activity_type_id)).where(
                # ✅ case-insensitive "APPROVED"
                func.lower(cast(ActivitySession.status, String)) == "approved",
                ActivitySession.activity_type_id.is_not(None),

                # ✅ overlap (NOT started_at inside window)
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )
        activity_type_ids = sorted({int(r[0]) for r in aq.all() if r and r[0] is not None})

    if not activity_type_ids:
        return {
            "event_id": event_id,
            "eligible_students": 0,
            "submissions_approved": 0,
            "certificates_issued": 0,
        }

    # ✅ Get eligible students (your helper already uses overlap + NULL-end fallback)
    eligible_student_ids = await _eligible_students_from_sessions(db, event, activity_type_ids)
    eligible_student_ids = sorted({int(x) for x in (eligible_student_ids or []) if x is not None})

    if not eligible_student_ids:
        return {
            "event_id": event_id,
            "eligible_students": 0,
            "submissions_approved": 0,
            "certificates_issued": 0,
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
            sub = EventSubmission(event_id=event_id, student_id=sid, status="approved")
            if hasattr(sub, "submitted_at"):
                sub.submitted_at = now_utc
            if hasattr(sub, "approved_at"):
                sub.approved_at = now_utc
            db.add(sub)
            submissions_approved += 1
        else:
            # ✅ normalize: anything that's not already approved -> approve it (expired/pending/rejected/etc.)
            if (sub.status or "").lower() != "approved":
                sub.status = "approved"
                if hasattr(sub, "submitted_at") and getattr(sub, "submitted_at", None) is None:
                    sub.submitted_at = now_utc
                if hasattr(sub, "approved_at"):
                    sub.approved_at = now_utc
                submissions_approved += 1

    await db.commit()

    # ✅ Issue certificates for approved/expired submissions
    issued = await _issue_certificates_for_event(db, event)

    return {
        "event_id": event_id,
        "eligible_students": len(eligible_student_ids),
        "submissions_approved": submissions_approved,
        "certificates_issued": issued,
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
        ActivitySession.submitted_at,
        ActivitySession.expires_at,
        end_utc,  # ✅ fallback prevents NULL end from breaking overlap logic
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


async def _issue_certificates_for_event(db: AsyncSession, event: Event) -> int:
    """
    ✅ FIXED PERMANENTLY:
    - EventSubmission.status + ActivitySession.status are matched case-insensitively
      (handles DB enums stored as "APPROVED" etc.)
    - Uses mapped activity_type_ids; if missing -> infer from APPROVED sessions in window
    - Computes HOURS by overlap inside event window:
        overlap = max(0, min(session_end, end_utc) - max(started_at, start_utc))
      where session_end = coalesce(submitted_at, expires_at, end_utc)  ✅ IMPORTANT FIX
      (prevents NULL end timestamps from producing 0 rows / 0 hours)
    - Issues certificate only if hours > 0 for that student + activity_type in window
    - If mapping exists but yields 0, retries with inferred ids (mapping mismatch safety)
    """

    # -----------------------
    # Approved submissions
    # -----------------------
    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.event_id == event.id,
            func.lower(cast(EventSubmission.status, String)).in_(["approved", "expired"]),
        )
    )
    submissions = q.scalars().all()
    if not submissions:
        return 0

    # -----------------------
    # Event window in UTC
    # -----------------------
    start_utc, end_utc = _event_window_utc(event)

    # Safety if old rows have bad end_time
    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(hours=6)

    # -----------------------
    # Activity types (mapped -> else infer)
    # -----------------------
    mapped_ids = await _get_event_activity_type_ids(db, event.id)
    activity_type_ids = sorted({int(x) for x in mapped_ids if x is not None})

    if not activity_type_ids:
        activity_type_ids = await _infer_activity_type_ids_from_sessions(db, start_utc, end_utc)

    if not activity_type_ids:
        raise HTTPException(
            status_code=400,
            detail="No activity types found for this event (mapping empty and no approved sessions in event window).",
        )

    # -----------------------
    # Caches
    # -----------------------
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

    # -----------------------
    # Helper: hours overlap
    # -----------------------
    async def _hours_in_window(student_id: int, at_id: int) -> float:
        session_end = func.coalesce(
            ActivitySession.submitted_at,
            ActivitySession.expires_at,
            end_utc,  # ✅ fallback prevents NULL end from breaking overlap logic
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
                            )
                            / 3600.0,
                        )
                    ),
                    0.0,
                )
            ).where(
                ActivitySession.student_id == student_id,
                ActivitySession.activity_type_id == at_id,

                # ✅ FIX: case-insensitive APPROVED match
                func.lower(cast(ActivitySession.status, String)) == "approved",

                # ✅ must overlap window (use same session_end)
                ActivitySession.started_at <= end_utc,
                session_end >= start_utc,
            )
        )
        return float(hrs_q.scalar() or 0.0)

    # -----------------------
    # Main issue loop
    # -----------------------
    issued = 0

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

            # already issued?
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

            # points
            points_awarded = 0
            if at:
                ppu = getattr(at, "points_per_unit", None)
                hpu = getattr(at, "hours_per_unit", None)
                if ppu is not None and hpu:
                    try:
                        points_awarded = int(round((hours / float(hpu)) * float(ppu)))
                    except Exception:
                        points_awarded = 0

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

    # -----------------------
    # Mapping mismatch retry
    # -----------------------
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

                    points_awarded = 0
                    if at:
                        ppu = getattr(at, "points_per_unit", None)
                        hpu = getattr(at, "hours_per_unit", None)
                        if ppu is not None and hpu:
                            try:
                                points_awarded = int(round((hours / float(hpu)) * float(ppu)))
                            except Exception:
                                points_awarded = 0

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

    await db.commit()
    return issued


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

    # ✅ Optional: only delete for approved/expired submissions (safer)
    subq = await db.execute(
        select(EventSubmission.id).where(
            EventSubmission.event_id == event_id,
            func.lower(cast(EventSubmission.status, String)).in_(["approved", "expired"]),
        )
    )
    sub_ids = [int(x) for x in subq.scalars().all()]

    if sub_ids:
        await db.execute(sql_delete(Certificate).where(Certificate.submission_id.in_(sub_ids)))
        await db.commit()

    # ✅ re-issue
    issued = await _issue_certificates_for_event(db, event)

    if issued == 0:
        raise HTTPException(
            status_code=400,
            detail="No certificates generated. Ensure approved submissions exist and approved sessions exist within the event window for the mapped activity types.",
        )

    return {"event_id": event_id, "certificates_issued": issued}


# =========================================================
# ---------------------- THUMBNAIL -------------------------
# =========================================================

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


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

    minio = get_minio()

    bucket = os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS", "vikasana-event-thumbnails")
    ensure_bucket(minio, bucket)

    ext = file.filename.split(".")[-1].lower() if "." in file.filename else "jpg"
    object_name = f"thumbnails/{admin_id}/{uuid.uuid4().hex}.{ext}"

    minio.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )

    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    if not public_base:
        public_base = "http://31.97.230.171:9000"

    public_url = f"{public_base}/{bucket}/{object_name}"

    return {
        "object_name": object_name,
        "public_url": public_url,
        "content_type": content_type,
        "size": len(data),
    }

# =========================================================
# ---------------------- ADMIN -----------------------------
# =========================================================
async def create_event(db: AsyncSession, payload) -> dict:
    """
    ✅ UPDATED create_event:
    - end_time is OPTIONAL
    - if end_time missing → defaults to +24 hours from start_time (window logic handles next-day)
    - No nested transaction
    - Validates ActivityType IDs exist
    - Inserts Event + mappings atomically with ONE commit
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

    # ✅ if end_time missing → default 24 hours from start_time
    # (stored as TIME; window helpers treat end<=start as next day)
    if end_time is None:
        end_time = start_time

    # If admin DID provide end_time, validate it
    # (same-day validation only; cross-midnight support comes from window helper)
    if getattr(payload, "end_time", None) is not None and end_time <= start_time:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")

    # ─────────────────────────────────────────────
    # required_photos safety
    # ─────────────────────────────────────────────
    required_photos = int(getattr(payload, "required_photos", 3) or 3)
    if required_photos < 3 or required_photos > 5:
        raise HTTPException(status_code=422, detail="required_photos must be between 3 and 5")

    # ─────────────────────────────────────────────
    # Activity type ids (ONLY from schema)
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
    # Create event + mapping in ONE transaction
    # ─────────────────────────────────────────────
    try:
        event = Event(
            title=str(getattr(payload, "title", "")).strip(),
            description=(getattr(payload, "description", None) or None),
            required_photos=required_photos,
            is_active=True,
            event_date=event_date,
            start_time=start_time,
            end_time=end_time,  # ✅ may equal start_time when omitted (means +24h in window helper)
            thumbnail_url=getattr(payload, "thumbnail_url", None),
            venue_name=getattr(payload, "venue_name", None),
            maps_url=maps_url,
            location_lat=getattr(payload, "location_lat", None),
            location_lng=getattr(payload, "location_lng", None),
            geo_radius_m=getattr(payload, "geo_radius_m", None),
        )
        db.add(event)
        await db.flush()  # ✅ event.id available

        db.add_all([EventActivityType(event_id=event.id, activity_type_id=at_id) for at_id in ids])

        await db.commit()
        await db.refresh(event)

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
            "activity_type_ids": ids,
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
      - end_time > start_time (same day)
      - activity_type_ids must exist if provided
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # -----------------------
    # Extract incoming fields
    # -----------------------
    title = getattr(payload, "title", None)
    description = getattr(payload, "description", None)
    thumbnail_url = getattr(payload, "thumbnail_url", None)
    venue_name = getattr(payload, "venue_name", None)
    maps_url = getattr(payload, "maps_url", None) or getattr(payload, "venue_maps_url", None)

    location_lat = getattr(payload, "location_lat", None)
    location_lng = getattr(payload, "location_lng", None)
    geo_radius_m = getattr(payload, "geo_radius_m", None)

    is_active = getattr(payload, "is_active", None)

    # date/time may come in different keys
    new_event_date = _parse_date(getattr(payload, "event_date", None) or getattr(payload, "date", None))
    new_start_time = _parse_time(getattr(payload, "start_time", None) or getattr(payload, "time", None))
    new_end_time = _parse_time(getattr(payload, "end_time", None))

    required_photos_in = getattr(payload, "required_photos", None)

    # activity types (optional)
    activity_type_ids_raw = getattr(payload, "activity_type_ids", None)
    replace_mappings = activity_type_ids_raw is not None  # if provided, we replace

    # -----------------------
    # Apply updates to model
    # -----------------------
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

    if is_active is not None:
        event.is_active = bool(is_active)

    # required_photos validation
    if required_photos_in is not None:
        rp = int(required_photos_in)
        if rp < 3 or rp > 5:
            raise HTTPException(status_code=422, detail="required_photos must be between 3 and 5")
        event.required_photos = rp

    # apply date/time (partial)
    if new_event_date is not None:
        event.event_date = new_event_date
    if new_start_time is not None:
        event.start_time = new_start_time
    if new_end_time is not None:
        event.end_time = new_end_time

    # validate time window if any changed OR if existing is incomplete
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
        if et <= st:
            raise HTTPException(status_code=422, detail="end_time must be after start_time")

    # -----------------------
    # Replace mappings if provided
    # -----------------------
    new_ids: list[int] = []
    if replace_mappings:
        new_ids = sorted({int(x) for x in (activity_type_ids_raw or []) if x is not None and int(x) > 0})
        if not new_ids:
            raise HTTPException(status_code=422, detail="Please select at least 1 activity type")

        q = await db.execute(select(ActivityType.id).where(ActivityType.id.in_(new_ids)))
        existing = {int(r[0]) for r in q.all()}
        missing = [i for i in new_ids if i not in existing]
        if missing:
            raise HTTPException(status_code=422, detail=f"Invalid activity_type_ids: {missing}")

        try:
            await db.execute(sql_delete(EventActivityType).where(EventActivityType.event_id == event_id))
            db.add_all([EventActivityType(event_id=event_id, activity_type_id=at_id) for at_id in new_ids])
        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update activity mappings: {str(e)}")

    # -----------------------
    # Commit
    # -----------------------
    try:
        await db.commit()
        await db.refresh(event)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update event: {str(e)}")

    # fetch ids if not replaced (for response)
    if not replace_mappings:
        mapped_ids = await _get_event_activity_type_ids(db, event_id)
        new_ids = sorted({int(x) for x in mapped_ids if x is not None})

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
        "activity_type_ids": new_ids,
    }
    

async def end_event(db: AsyncSession, event_id: int):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # ✅ set inactive
    event.is_active = False

    # ✅ set end_time = NOW (IST) so window becomes correct
    now_ist = _now_ist_aware()
    event.end_time = now_ist.time().replace(tzinfo=None)

    # Expire only unfinished submissions
    await db.execute(
        update(EventSubmission)
        .where(
            EventSubmission.event_id == event_id,
            EventSubmission.status.in_(["in_progress", "draft"]),
        )
        .values(status="expired")
    )

    await db.commit()
    await db.refresh(event)
    return event


async def delete_event(db: AsyncSession, event_id: int) -> None:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    await db.execute(sql_delete(EventActivityType).where(EventActivityType.event_id == event_id))

    sub_result = await db.execute(select(EventSubmission.id).where(EventSubmission.event_id == event_id))
    submission_ids = [row[0] for row in sub_result.fetchall()]

    if submission_ids:
        await db.execute(
            sql_delete(EventSubmissionPhoto).where(EventSubmissionPhoto.submission_id.in_(submission_ids))
        )
        await db.execute(sql_delete(EventSubmission).where(EventSubmission.event_id == event_id))

    await db.execute(sql_delete(Event).where(Event.id == event_id))
    await db.commit()



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

    if submission.status != "submitted":
        raise HTTPException(status_code=400, detail="Only submitted items can be approved")

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
    await _issue_certificates_for_event(db, event)

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

    if submission.status != "submitted":
        raise HTTPException(status_code=400, detail="Only submitted items can be rejected")

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
    Returns ALL events (upcoming + ongoing + past) so the frontend
    can classify them into tabs using deriveStatus().
    No time-window filtering here — that was causing empty results.
    """
    try:
        q = await db.execute(
            select(Event)
            .where(Event.event_date.isnot(None))
            .order_by(Event.event_date.desc(), Event.start_time.asc().nulls_last(), Event.id.desc())
        )
        events = q.scalars().all()
        return events  # ✅ Return ALL, frontend handles Upcoming/Ongoing/Past tabs

    except Exception as e:
        print(f"Error fetching events: {str(e)}")
        return []

async def register_for_event(db: AsyncSession, student_id: int, event_id: int):
    q = await db.execute(select(Event).where(Event.id == event_id))
    event = q.scalar_one_or_none()

    if not event or not getattr(event, "is_active", True):
        raise HTTPException(status_code=404, detail="Event not found")

    _ensure_event_window(event)

    q = await db.execute(
        select(EventSubmission).where(
            EventSubmission.event_id == event_id,
            EventSubmission.student_id == student_id,
        )
    )
    existing = q.scalar_one_or_none()
    if existing:
        return {"submission_id": existing.id, "status": existing.status}

    submission = EventSubmission(
        event_id=event_id,
        student_id=student_id,
        status="in_progress",
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    return {"submission_id": submission.id, "status": submission.status}


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
        await _issue_certificates_for_event(db, event)

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