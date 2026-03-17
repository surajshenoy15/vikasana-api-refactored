# app/controllers/activity_photos_controller.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession
from app.features.activities.models import ActivityType
from app.features.events.models import Event

import math

DEFAULT_RADIUS_M = 500


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Great-circle distance in meters between two lat/lng points.
    """
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


async def _strict_geofence_check(
    db: AsyncSession,
    session: ActivitySession,
    lat: Optional[float],
    lng: Optional[float],
) -> Tuple[Optional[float], bool, Optional[str]]:
    """
    STRICT POLICY:
    - If ActivityType has target_lat/target_lng => enforce radius_m
    - If GPS missing => reject
    - If outside => reject

    Returns (distance_m, is_in_geofence, geo_flag_reason)
    """
    # activity type must exist for geofence policy
    at_res = await db.execute(select(ActivityType).where(ActivityType.id == session.activity_type_id))
    at = at_res.scalar_one_or_none()

    # If no activity type found, allow (shouldn't happen)
    if not at:
        return None, True, None

    target_lat = getattr(at, "target_lat", None)
    target_lng = getattr(at, "target_lng", None)
    radius_m = int(getattr(at, "radius_m", DEFAULT_RADIUS_M) or DEFAULT_RADIUS_M)

    # If admin didn't configure target, allow
    if target_lat is None or target_lng is None:
        return None, True, None

    # Admin configured target => GPS must be present
    if lat is None or lng is None:
        raise HTTPException(
            status_code=400,
            detail="Location is required for this activity. Please enable GPS and try again.",
        )

    distance_m = _haversine_m(float(lat), float(lng), float(target_lat), float(target_lng))

    # Outside => reject
    if distance_m > radius_m:
        raise HTTPException(
            status_code=400,
            detail=f"Outside allowed area. You are ~{int(distance_m)}m away. Allowed radius is {radius_m}m.",
        )

    return float(distance_m), True, None


async def add_activity_photo(
    db: AsyncSession,
    session_id: int,
    student_id: int,
    seq_no: int,
    image_url: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    captured_at: Optional[datetime] = None,
    sha256: Optional[str] = None,
    # ✅ NEW: accept these so callers can pass without TypeError
    distance_m: Optional[float] = None,
    is_in_geofence: Optional[bool] = None,
    geo_flag_reason: Optional[str] = None,
):
    """
    Adds (or updates) a photo row for a session.

    ✅ STRICT geofence enforcement:
    - If activity_type has target location => upload allowed ONLY within radius_m (default 500m)
    - If GPS missing => reject
    - If outside => reject

    NOTE:
    - distance_m / is_in_geofence / geo_flag_reason may be passed by caller.
      If not passed, this function computes them.
    """

    # 1) Validate session belongs to student
    res = await db.execute(
        select(ActivitySession).where(
            ActivitySession.id == session_id,
            ActivitySession.student_id == student_id,
        )
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found for this student")

    # 2) Ensure session is DRAFT (Enum-safe)
    status_val = session.status.value if hasattr(session.status, "value") else str(session.status)
    status_val = (status_val or "").upper()
    if status_val != "DRAFT":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot upload photos when session status is {status_val}",
        )

    # 3) Validate seq_no vs required_photos ONLY if session truly uses event_id
    if getattr(session, "event_id", None):
        rq = await db.execute(select(Event.required_photos).where(Event.id == session.event_id))
        required_photos = rq.scalar_one_or_none()
        if required_photos is None:
            raise HTTPException(status_code=400, detail="Invalid event linked to session")

        required_photos = int(required_photos)
        if seq_no < 1 or seq_no > required_photos:
            raise HTTPException(status_code=400, detail=f"seq_no must be between 1 and {required_photos}")

    # 4) Upsert by (session_id, seq_no)
    res = await db.execute(
        select(ActivityPhoto).where(
            ActivityPhoto.session_id == session_id,
            ActivityPhoto.seq_no == seq_no,
        )
    )
    existing = res.scalar_one_or_none()

    if captured_at is None:
        captured_at = datetime.now(timezone.utc)

    # 5) Duplicate detection (sha256)
    is_duplicate = False
    has_sha_col = hasattr(ActivityPhoto, "sha256")
    if sha256 and has_sha_col:
        q = select(ActivityPhoto.id).where(
            and_(
                ActivityPhoto.session_id == session_id,
                ActivityPhoto.sha256 == sha256,
            )
        )
        if existing is not None:
            q = q.where(ActivityPhoto.id != existing.id)
        dup = await db.execute(q)
        is_duplicate = dup.scalar_one_or_none() is not None

    # ✅ 6) STRICT Geofence check BEFORE saving (unless caller already computed)
    # We still enforce strict policy (it will raise if missing/outside)
    if distance_m is None or is_in_geofence is None:
        distance_m2, ok2, reason2 = await _strict_geofence_check(db, session, lat, lng)
        if distance_m is None:
            distance_m = distance_m2
        if is_in_geofence is None:
            is_in_geofence = ok2
        if geo_flag_reason is None:
            geo_flag_reason = reason2

    # Column existence guards (in case migrations differ across envs)
    has_dist = hasattr(ActivityPhoto, "distance_m")
    has_in = hasattr(ActivityPhoto, "is_in_geofence")
    has_reason = hasattr(ActivityPhoto, "geo_flag_reason")

    try:
        if existing:
            existing.image_url = image_url
            existing.lat = float(lat) if lat is not None else None
            existing.lng = float(lng) if lng is not None else None
            existing.captured_at = captured_at
            existing.student_id = student_id

            if sha256 is not None and has_sha_col:
                existing.sha256 = sha256

            # ✅ store geofence fields if columns exist
            if has_dist:
                existing.distance_m = distance_m
            if has_in:
                existing.is_in_geofence = bool(is_in_geofence) if is_in_geofence is not None else True
            if has_reason:
                existing.geo_flag_reason = geo_flag_reason

            await db.commit()
            await db.refresh(existing)
            photo = existing

        else:
            payload = dict(
                session_id=session_id,
                student_id=student_id,
                seq_no=seq_no,
                image_url=image_url,
                lat=float(lat) if lat is not None else None,
                lng=float(lng) if lng is not None else None,
                captured_at=captured_at,
            )
            if sha256 is not None and has_sha_col:
                payload["sha256"] = sha256

            # ✅ include geofence fields only if columns exist
            if has_dist:
                payload["distance_m"] = distance_m
            if has_in:
                payload["is_in_geofence"] = bool(is_in_geofence) if is_in_geofence is not None else True
            if has_reason:
                payload["geo_flag_reason"] = geo_flag_reason

            photo = ActivityPhoto(**payload)
            db.add(photo)
            await db.commit()
            await db.refresh(photo)

        return {
            "id": photo.id,
            "image_url": photo.image_url,
            "sha256": getattr(photo, "sha256", None),
            "captured_at": photo.captured_at,
            "lat": photo.lat,
            "lng": photo.lng,
            "distance_m": getattr(photo, "distance_m", None),
            "is_in_geofence": bool(getattr(photo, "is_in_geofence", True)),
            "geo_flag_reason": getattr(photo, "geo_flag_reason", None),
            "is_duplicate": bool(is_duplicate),
        }

    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Photo for this seq_no already exists")
    except HTTPException:
        # geofence errors etc.
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save photo: {e}")