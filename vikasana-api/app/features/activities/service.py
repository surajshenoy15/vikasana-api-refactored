# app/controllers/activity_controller.py

import secrets
import math
from datetime import datetime, time, timezone
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.features.activities.models import ActivityType, ActivityTypeStatus
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityPhoto
from app.features.activities.models import StudentActivityStats  # kept (used in future)
from app.features.activities.photos_service import add_activity_photo
from app.features.activities.schemas.activity import PhotoOut

MIN_PHOTOS = 3
MAX_PHOTOS = 5
DEFAULT_RADIUS_M = 500


# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────

def _end_of_day(dt: datetime) -> datetime:
    return datetime.combine(dt.date(), time(23, 59, 59), tzinfo=dt.tzinfo)


def _calc_duration_hours(photo_times: list[datetime]) -> float:
    if len(photo_times) < 2:
        return 0.0
    start = min(photo_times)
    end = max(photo_times)
    seconds = (end - start).total_seconds()
    return max(0.0, seconds / 3600.0)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Great-circle distance in meters between two lat/lng points.
    """
    R = 6371000.0  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c



async def _get_activity_type_for_session(db: AsyncSession, session: ActivitySession) -> ActivityType | None:
    """
    Async-safe: DO NOT touch session.activity_type (lazy load causes MissingGreenlet).
    Always fetch ActivityType explicitly by activity_type_id.
    """
    at_id = getattr(session, "activity_type_id", None)
    if not at_id:
        return None

    res = await db.execute(select(ActivityType).where(ActivityType.id == at_id))
    return res.scalar_one_or_none()


def _compute_geofence_verdict(
    at: Optional[ActivityType],
    lat: Optional[float],
    lng: Optional[float],
) -> Tuple[Optional[float], bool, Optional[str], int, Optional[float], Optional[float]]:
    """
    Returns:
      (distance_m, is_in_geofence, geo_flag_reason, radius_m, target_lat, target_lng)

    Policy:
    - If target is not configured => allow (is_in_geofence=True)
    - If target configured but lat/lng missing => reject later (geo_flag_reason="missing_gps")
    - If outside => is_in_geofence=False, geo_flag_reason="outside_geofence>XYZm"
    """
    if not at:
        return None, True, None, DEFAULT_RADIUS_M, None, None

    target_lat = getattr(at, "target_lat", None)
    target_lng = getattr(at, "target_lng", None)
    radius_m = int(getattr(at, "radius_m", DEFAULT_RADIUS_M) or DEFAULT_RADIUS_M)

    # If admin didn't configure location, don't block
    if target_lat is None or target_lng is None:
        return None, True, None, radius_m, None, None

    # Admin configured location but GPS not available
    if lat is None or lng is None:
        return None, False, "missing_gps", radius_m, float(target_lat), float(target_lng)

    distance_m = _haversine_m(float(lat), float(lng), float(target_lat), float(target_lng))
    if distance_m > radius_m:
        return distance_m, False, f"outside_geofence>{radius_m}m", radius_m, float(target_lat), float(target_lng)

    return distance_m, True, None, radius_m, float(target_lat), float(target_lng)


# ─────────────────────────────────────────────
# Activity Types
# ─────────────────────────────────────────────

async def list_activity_types(db: AsyncSession, include_pending: bool = False):
    q = select(ActivityType).where(ActivityType.is_active == True)
    if not include_pending:
        q = q.where(ActivityType.status == ActivityTypeStatus.APPROVED)
    q = q.order_by(ActivityType.name.asc())
    res = await db.execute(q)
    return res.scalars().all()


async def request_new_activity_type(db: AsyncSession, name: str, description: str | None):
    existing = await db.execute(
        select(ActivityType).where(func.lower(ActivityType.name) == name.lower())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Activity type already exists")

    at = ActivityType(
        name=name.strip(),
        description=description,
        status=ActivityTypeStatus.PENDING,
        hours_per_unit=20,
        points_per_unit=5,
        max_points=20,
        # Geofence defaults already on model (radius_m=500)
    )
    db.add(at)
    await db.commit()
    await db.refresh(at)
    return at


# ─────────────────────────────────────────────
# Create Session
# ─────────────────────────────────────────────

async def create_session(
    db: AsyncSession,
    student_id: int,
    activity_type_id: int,
    activity_name: str,
    description: str | None,
):
    at_res = await db.execute(
        select(ActivityType).where(ActivityType.id == activity_type_id)
    )
    activity_type = at_res.scalar_one_or_none()
    if not activity_type:
        raise HTTPException(status_code=404, detail="Activity type not found")

    now = datetime.now(timezone.utc)

    session = ActivitySession(
        student_id=student_id,
        activity_type_id=activity_type_id,
        activity_name=activity_name.strip(),
        description=description,
        session_code=secrets.token_hex(8),
        started_at=now,
        expires_at=_end_of_day(now),
        status=ActivitySessionStatus.DRAFT,
    )

    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


# ─────────────────────────────────────────────
# List Sessions
# ─────────────────────────────────────────────

async def list_student_sessions(db: AsyncSession, student_id: int):
    """
    Student app history.
    Source of truth: EventSubmission + Event.

    This hides deleted events because we only return rows that still have
    a matching active Event row.
    """

    from app.features.events.models import Event, EventSubmission
    from app.features.certificates.models import Certificate

    event_conditions = []

    if hasattr(Event, "is_deleted"):
        event_conditions.append(Event.is_deleted == False)

    if hasattr(Event, "deleted_at"):
        event_conditions.append(Event.deleted_at.is_(None))

    if hasattr(Event, "is_active"):
        event_conditions.append(Event.is_active == True)

    stmt = (
        select(
            EventSubmission.id.label("submission_id"),
            EventSubmission.event_id.label("event_id"),
            EventSubmission.student_id.label("student_id"),
            EventSubmission.submitted_at.label("submitted_at"),
            EventSubmission.status.label("status"),

            Event.title.label("event_title"),
            Event.event_date.label("event_date"),
            Event.start_time.label("event_start_time"),
            Event.venue_name.label("event_location"),
            Event.category.label("event_category"),
        )
        .select_from(EventSubmission)
        .join(Event, Event.id == EventSubmission.event_id)
        .where(
            EventSubmission.student_id == student_id,
            EventSubmission.submitted_at.isnot(None),
            *event_conditions,
        )
        .order_by(
            EventSubmission.submitted_at.desc(),
            EventSubmission.id.desc(),
        )
    )

    rows = (await db.execute(stmt)).all()

    if not rows:
        return []

    submission_ids = [r.submission_id for r in rows]

    cert_set = set()
    if submission_ids:
        cert_q = await db.execute(
            select(Certificate.submission_id).where(
                Certificate.submission_id.in_(submission_ids)
            )
        )
        cert_set = {x[0] for x in cert_q.all()}

    result = []

    for r in rows:
        raw_status = getattr(r.status, "value", r.status)
        status_value = str(raw_status or "").upper()

        event_title = (r.event_title or "").strip()
        if not event_title:
            event_title = f"Event #{r.event_id}"

        # Use event points if your Event model has points/max_points.
        event_obj_points = 0
        try:
            # If Event.points exists in your model, use separate query-safe fallback.
            pass
        except Exception:
            pass

        points_awarded = 0

        # Certificate exists means generated/approved record.
        # If you want exact points, use the same formula/source used for /students/me.
        if status_value == "APPROVED":
            points_awarded = 0

            # Optional: if Event has points column, fetch it separately below.
            ev_points_q = await db.execute(
                select(Event.points).where(Event.id == r.event_id)
            ) if hasattr(Event, "points") else None

            if ev_points_q is not None:
                points_awarded = int(ev_points_q.scalar() or 0)

        result.append(
            {
                "id": r.submission_id,
                "session_id": r.submission_id,
                "submission_id": r.submission_id,

                "event_id": r.event_id,
                "activity_type_id": None,

                "event_title": event_title,
                "title": event_title,
                "activity_name": event_title,

                "event_category": r.event_category or "Event",
                "category": r.event_category or "Event",

                "event_date": r.event_date.isoformat() if r.event_date else None,
                "event_start_time": str(r.event_start_time) if r.event_start_time else None,
                "event_location": r.event_location,

                "status": status_value,
                "started_at": None,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "duration_hours": 0,
                "flag_reason": None,

                "certificate": r.submission_id in cert_set,

                "points_awarded": points_awarded,
                "awarded_points": points_awarded,
                "points": points_awarded,
            }
        )

    return result
# ─────────────────────────────────────────────
# Add Photo (STRICT 500m geofence enforcement)
# ─────────────────────────────────────────────

async def add_photo_to_session(
    db: AsyncSession,
    student_id: int,
    session_id: int,
    seq_no: int,
    image_url: str,
    captured_at: datetime,
    lat: float,
    lng: float,
    sha256: str | None = None,
) -> PhotoOut:
    # 1) Verify session exists + belongs to student (load activity_type optionally)
    res = await db.execute(
        select(ActivitySession)
        .where(
            ActivitySession.id == session_id,
            ActivitySession.student_id == student_id,
        )
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 2) Ensure still draft
    if session.status != ActivitySessionStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Cannot add photo after submission")

    # 3) Ensure not expired
    now = datetime.now(timezone.utc)
    if now > session.expires_at:
        session.status = ActivitySessionStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=400, detail="Session expired")

    # 4) Enforce seq range
    if seq_no < 1 or seq_no > MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"seq_no must be between 1 and {MAX_PHOTOS}")

    # 5) Prevent exceeding max photos count (count-based guard)
    count_res = await db.execute(
        select(func.count(ActivityPhoto.id)).where(ActivityPhoto.session_id == session_id)
    )
    existing_count = int(count_res.scalar() or 0)
    if existing_count >= MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_PHOTOS} photos allowed")

    # 6) STRICT geofence:
    #    - if target configured AND outside => reject immediately
    #    - if target configured AND gps missing => reject immediately
    at = await _get_activity_type_for_session(db, session)

    distance_m, is_in_geofence, geo_flag_reason, radius_m, target_lat, target_lng = _compute_geofence_verdict(
        at=at,
        lat=lat,
        lng=lng,
    )

    if at and getattr(at, "target_lat", None) is not None and getattr(at, "target_lng", None) is not None:
        # target configured => enforce
        if not is_in_geofence:
            if geo_flag_reason == "missing_gps":
                raise HTTPException(
                    status_code=400,
                    detail="Location is required for this activity. Please enable GPS and try again.",
                )
            raise HTTPException(
                status_code=400,
                detail=f"Outside allowed area. You are ~{int(distance_m or 0)}m away. Allowed radius is {radius_m}m.",
            )

    # 7) Save via photo controller (stores geo verdict too)
    # NOTE: Ensure ActivityPhoto model has columns: distance_m, is_in_geofence, geo_flag_reason
    row = await add_activity_photo(
        db=db,
        session_id=session_id,
        student_id=student_id,
        seq_no=seq_no,
        image_url=image_url,
        lat=lat,
        lng=lng,
        captured_at=captured_at,
        sha256=sha256,
        distance_m=distance_m,
        is_in_geofence=True,          # strict passed => always True if target configured
        geo_flag_reason=None,
    )
    return row


# ─────────────────────────────────────────────
# Submit Session (strict: require MIN_PHOTOS + all in geofence)
# ─────────────────────────────────────────────

async def submit_session(db: AsyncSession, student_id: int, session_id: int):
    res = await db.execute(
        select(ActivitySession).where(
            ActivitySession.id == session_id,
            ActivitySession.student_id == student_id,
        )
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != ActivitySessionStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Session already submitted")

    now = datetime.now(timezone.utc)
    if now > session.expires_at:
        session.status = ActivitySessionStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=400, detail="Session expired")

    # Load ActivityType for dynamic radius message
    at = await _get_activity_type_for_session(db, session)
    radius_m = int(getattr(at, "radius_m", DEFAULT_RADIUS_M) or DEFAULT_RADIUS_M) if at else DEFAULT_RADIUS_M

    # ✅ Only consider in-geofence photos as valid (and strict add_photo already enforces)
    photos_res = await db.execute(
        select(ActivityPhoto).where(
            ActivityPhoto.session_id == session_id,
            ActivityPhoto.is_in_geofence == True,
        )
    )
    photos = photos_res.scalars().all()

    if len(photos) < MIN_PHOTOS:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum {MIN_PHOTOS} photos required within {radius_m}m radius",
        )

    photo_times: list[datetime] = []
    suspicious = False
    reasons: list[str] = []

    for ph in photos:
        if ph.captured_at is None:
            suspicious = True
            reasons.append("photo_missing_timestamp")
            continue

        photo_times.append(ph.captured_at)

        if ph.captured_at.date() != session.started_at.date():
            suspicious = True
            reasons.append("photo_not_same_day")

        if not (session.started_at <= ph.captured_at <= session.expires_at):
            suspicious = True
            reasons.append("photo_outside_time_window")

        if getattr(ph, "sha256", None):
            dup2 = await db.execute(
                select(ActivityPhoto.id).where(
                    ActivityPhoto.session_id == session_id,
                    ActivityPhoto.sha256 == ph.sha256,
                    ActivityPhoto.id != ph.id,
                )
            )
            if dup2.scalar_one_or_none() is not None:
                suspicious = True
                reasons.append("duplicate_photo_detected")

    session.duration_hours = _calc_duration_hours(photo_times)
    session.submitted_at = now

    if suspicious:
        session.status = ActivitySessionStatus.FLAGGED
        session.flag_reason = ",".join(sorted(set(reasons)))
        await db.commit()
        return session, 0, 0, 0

    # Note: You might want SUBMITTED instead of APPROVED, depending on your workflow.
    # Keeping your original logic.
    session.status = ActivitySessionStatus.APPROVED
    await db.commit()

    return session, 0, 0, 0


# ─────────────────────────────────────────────
# Session Detail (includes geo info + target location)
# ─────────────────────────────────────────────

async def get_student_session_detail(db: AsyncSession, student_id: int, session_id: int):
    res = await db.execute(
        select(ActivitySession)
        .where(
            ActivitySession.id == session_id,
            ActivitySession.student_id == student_id,
        )
        .options(selectinload(ActivitySession.photos))
    )

    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    at = await _get_activity_type_for_session(db, session)

    photos = list(session.photos or [])

    sha_counts: dict[str, int] = {}
    for ph in photos:
        if getattr(ph, "sha256", None):
            sha_counts[ph.sha256] = sha_counts.get(ph.sha256, 0) + 1

    photos_out = []
    for ph in photos:
        photos_out.append(
            {
                "id": ph.id,
                "image_url": ph.image_url,
                "sha256": getattr(ph, "sha256", None),
                "captured_at": ph.captured_at,
                "lat": ph.lat,
                "lng": ph.lng,
                "distance_m": getattr(ph, "distance_m", None),
                "is_in_geofence": bool(getattr(ph, "is_in_geofence", True)),
                "geo_flag_reason": getattr(ph, "geo_flag_reason", None),
                "is_duplicate": bool(getattr(ph, "sha256", None) and sha_counts.get(ph.sha256, 0) > 1),
            }
        )

    return {
        "id": session.id,
        "activity_type_id": session.activity_type_id,
        "activity_name": session.activity_name,
        "description": session.description,
        "started_at": session.started_at,
        "expires_at": session.expires_at,
        "submitted_at": session.submitted_at,
        "status": session.status.value if hasattr(session.status, "value") else str(session.status),
        "duration_hours": session.duration_hours,
        "flag_reason": session.flag_reason,
        "target_location": {
            "maps_url": getattr(at, "maps_url", None) if at else None,
            "target_lat": getattr(at, "target_lat", None) if at else None,
            "target_lng": getattr(at, "target_lng", None) if at else None,
            "radius_m": int(getattr(at, "radius_m", DEFAULT_RADIUS_M) or DEFAULT_RADIUS_M) if at else DEFAULT_RADIUS_M,
        },
        "photos": photos_out,
    }