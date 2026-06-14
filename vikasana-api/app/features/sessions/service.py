# app/controllers/admin_sessions_controller.py
from __future__ import annotations

from typing import Optional
from datetime import timedelta
import os
import anyio

from fastapi import HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.features.students.points_service import award_points_for_session
from app.core.config import settings
from app.core.minio_client import get_public_minio

from app.features.activities.models import ActivityFaceCheck
from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType
from app.features.students.models import Student


# ─────────────────────────────────────────────────────────────
# Storage presign config
# ─────────────────────────────────────────────────────────────

_PRESIGN_EXPIRY = 3600  # 1 hour


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _activity_bucket() -> str:
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_ACTIVITIES", "activity-uploads")

    return _env(
        "MINIO_BUCKET_ACTIVITIES",
        getattr(settings, "MINIO_BUCKET_ACTIVITIES", "activity-uploads"),
    )


def _face_bucket() -> str:
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_FACE", "face-verification")

    return _env(
        "MINIO_FACE_BUCKET",
        getattr(settings, "MINIO_FACE_BUCKET", "face-verification"),
    )


def _extract_object_key(url: str, bucket: str) -> str:
    """
    Strip protocol + host + bucket from a URL or return the object key directly.

    Supports:
    - activities/1/2/file.jpg
    - activity-uploads/activities/1/2/file.jpg
    - face-verification/1/2_boxed.jpg
    - https://domain.com/activity-uploads/activities/1/2/file.jpg
    - https://bucket.s3.ap-south-1.amazonaws.com/activities/1/2/file.jpg
    """
    s = (url or "").strip().replace("\\", "/")

    if not s:
        return s

    # Remove query params from presigned URLs
    s = s.split("?", 1)[0]

    # Full URL -> keep only path
    if "://" in s:
        from urllib.parse import urlparse

        parsed = urlparse(s)
        s = parsed.path.lstrip("/")

    bucket_names = {
        bucket,
        _env("MINIO_BUCKET_ACTIVITIES", "activity-uploads"),
        _env("MINIO_FACE_BUCKET", "face-verification"),
        _env("AWS_S3_BUCKET_ACTIVITIES", "activity-uploads"),
        _env("AWS_S3_BUCKET_FACE", "face-verification"),
    }

    changed = True
    while changed:
        changed = False
        for b in bucket_names:
            if b and s.startswith(b + "/"):
                s = s[len(b) + 1:]
                changed = True

    return s.lstrip("/")


async def _presign(bucket: str, key: str) -> Optional[str]:
    if not key:
        return None

    try:
        client = get_public_minio()

        url = await anyio.to_thread.run_sync(
            lambda: client.presigned_get_object(
                bucket_name=bucket,
                object_name=key,
                expires=timedelta(seconds=_PRESIGN_EXPIRY),
            )
        )

        return url

    except Exception:
        return None


async def _presign_activity(url: str) -> Optional[str]:
    bucket = _activity_bucket()
    key = _extract_object_key(url, bucket)
    return await _presign(bucket, key)


async def _presign_face(obj: str) -> Optional[str]:
    bucket = _face_bucket()
    key = _extract_object_key(obj, bucket)
    return await _presign(bucket, key)


# ─────────────────────────────────────────────────────────────
# Points helper
# ─────────────────────────────────────────────────────────────

def _calc_session_points(
    activity_type: Optional[ActivityType],
    duration_hours: Optional[float],
) -> int:
    """
    units = floor(duration_hours / hours_per_unit)
    points = min(units * points_per_unit, max_points)
    """
    if not activity_type or not duration_hours:
        return 0

    try:
        hpu = int(activity_type.hours_per_unit or 1)
        ppu = int(activity_type.points_per_unit or 0)
        mp = int(activity_type.max_points or 0)
        units = int(duration_hours / hpu)
        return min(units * ppu, mp)

    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# LIST sessions
# ─────────────────────────────────────────────────────────────

async def admin_list_sessions(
    db: AsyncSession,
    status: Optional[ActivitySessionStatus] = None,
    include_all: bool = False,
    q: Optional[str] = None,
    limit: int = 6000,
    offset: int = 0,
) -> list[dict]:
    """
    Behavior must match routes:

    - If include_all=True  -> NO status filter
    - Else if status is None -> Queue: SUBMITTED + FLAGGED
    - Else -> exact status filter
    """

    # ✅ allow up to 6000 records safely
    safe_limit = min(int(limit or 6000), 6000)
    safe_offset = max(int(offset or 0), 0)

    stmt = (
        select(ActivitySession)
        .options(selectinload(ActivitySession.photos))
        .order_by(
            ActivitySession.submitted_at.desc().nulls_last(),
            ActivitySession.id.desc(),
        )
        .limit(safe_limit)
        .offset(safe_offset)
    )

    if not include_all:
        if status is None:
            stmt = stmt.where(
                ActivitySession.status.in_(
                    [
                        ActivitySessionStatus.SUBMITTED,
                        ActivitySessionStatus.FLAGGED,
                    ]
                )
            )
        else:
            stmt = stmt.where(ActivitySession.status == status)

    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                ActivitySession.activity_name.ilike(like),
                ActivitySession.session_code.ilike(like),
            )
        )

    res = await db.execute(stmt)
    sessions = res.scalars().all()

    if not sessions:
        return []

    student_ids = list({s.student_id for s in sessions if s.student_id})
    students: dict[int, Student] = {}

    if student_ids:
        sr = await db.execute(select(Student).where(Student.id.in_(student_ids)))
        for st in sr.scalars().all():
            students[st.id] = st

    type_ids = list({s.activity_type_id for s in sessions if s.activity_type_id})
    activity_types: dict[int, ActivityType] = {}

    if type_ids:
        tr = await db.execute(select(ActivityType).where(ActivityType.id.in_(type_ids)))
        for at in tr.scalars().all():
            activity_types[at.id] = at

    session_ids = [s.id for s in sessions]
    face_checks: dict[int, ActivityFaceCheck] = {}

    if session_ids:
        subq = (
            select(
                ActivityFaceCheck.session_id,
                func.max(ActivityFaceCheck.id).label("max_id"),
            )
            .where(ActivityFaceCheck.session_id.in_(session_ids))
            .group_by(ActivityFaceCheck.session_id)
            .subquery()
        )

        fcr = await db.execute(
            select(ActivityFaceCheck).join(
                subq,
                (ActivityFaceCheck.session_id == subq.c.session_id)
                & (ActivityFaceCheck.id == subq.c.max_id),
            )
        )

        for fc in fcr.scalars().all():
            face_checks[fc.session_id] = fc

    rows: list[dict] = []

    for s in sessions:
        st = students.get(s.student_id)
        at = activity_types.get(s.activity_type_id)
        fc = face_checks.get(s.id)

        photos = list(s.photos or [])
        photo_times = [p.captured_at for p in photos if p.captured_at]
        in_time = min(photo_times) if photo_times else None
        out_time = max(photo_times) if photo_times else None

        points = _calc_session_points(at, s.duration_hours)

        face_processed_url = None
        if fc and fc.processed_object:
            face_processed_url = await _presign_face(fc.processed_object)

        rows.append(
            {
                "id": s.id,
                "student_id": s.student_id,
                "student_name": st.name if st else None,
                "usn": st.usn if st else None,
                "college": st.college if st else None,
                "activity_type_id": s.activity_type_id,
                "activity_name": s.activity_name,
                "description": s.description,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "started_at": s.started_at,
                "submitted_at": s.submitted_at,
                "expires_at": s.expires_at,
                "duration_hours": s.duration_hours,
                "flag_reason": s.flag_reason,
                "photos_count": len(photos),
                "in_time": in_time,
                "out_time": out_time,
                "total_activity_points": points,
                "activity_type": {
                    "id": at.id,
                    "name": at.name,
                    "points_per_unit": at.points_per_unit,
                    "max_points": at.max_points,
                    "hours_per_unit": at.hours_per_unit,
                }
                if at
                else None,
                "latest_face_matched": fc.matched if fc else None,
                "latest_face_reason": fc.reason if fc else None,
                "latest_face_processed_url": face_processed_url,
                "latest_face_check": {
                    "id": fc.id,
                    "matched": fc.matched,
                    "cosine_score": fc.cosine_score,
                    "l2_score": fc.l2_score,
                    "total_faces": fc.total_faces,
                    "reason": fc.reason,
                    "processed_object": fc.processed_object,
                }
                if fc
                else None,
            }
        )

    return rows


# ─────────────────────────────────────────────────────────────
# GET session detail
# ─────────────────────────────────────────────────────────────

async def admin_get_session_detail(
    db: AsyncSession,
    session_id: int,
) -> dict:
    res = await db.execute(
        select(ActivitySession)
        .where(ActivitySession.id == session_id)
        .options(selectinload(ActivitySession.photos))
    )

    s = res.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    st = await db.get(Student, s.student_id)
    at = await db.get(ActivityType, s.activity_type_id)

    fcr = await db.execute(
        select(ActivityFaceCheck)
        .where(ActivityFaceCheck.session_id == session_id)
        .order_by(ActivityFaceCheck.id.desc())
    )

    all_face_checks = fcr.scalars().all()
    latest_fc = all_face_checks[0] if all_face_checks else None

    photos = list(s.photos or [])
    photo_times = [p.captured_at for p in photos if p.captured_at]
    in_time = min(photo_times) if photo_times else None
    out_time = max(photo_times) if photo_times else None

    points = _calc_session_points(at, s.duration_hours)

    face_processed_url = None
    if latest_fc and latest_fc.processed_object:
        face_processed_url = await _presign_face(latest_fc.processed_object)

    photos_out = []

    for p in sorted(photos, key=lambda x: x.seq_no or 0):
        presigned = await _presign_activity(p.image_url)

        ph_fc = next((fc for fc in all_face_checks if fc.photo_id == p.id), None)

        ph_face_url = None
        if ph_fc and ph_fc.processed_object:
            ph_face_url = await _presign_face(ph_fc.processed_object)

        photos_out.append(
            {
                "id": p.id,
                "seq_no": p.seq_no,
                "image_url": presigned or p.image_url,
                "captured_at": p.captured_at,
                "lat": p.lat,
                "lng": p.lng,
                "distance_m": getattr(p, "distance_m", None),
                "is_in_geofence": bool(getattr(p, "is_in_geofence", True)),
                "geo_flag_reason": getattr(p, "geo_flag_reason", None),
                "sha256": getattr(p, "sha256", None),
                "face_matched": ph_fc.matched if ph_fc else None,
                "face_reason": ph_fc.reason if ph_fc else None,
                "face_processed_url": ph_face_url,
            }
        )

    location_trail = [
        {
            "seq_no": p["seq_no"],
            "lat": p["lat"],
            "lng": p["lng"],
            "captured_at": p["captured_at"],
            "is_in_geofence": p["is_in_geofence"],
            "distance_m": p["distance_m"],
        }
        for p in photos_out
        if p["lat"] is not None and p["lng"] is not None
    ]

    return {
        "id": s.id,
        "student_id": s.student_id,
        "student_name": st.name if st else None,
        "usn": st.usn if st else None,
        "college": st.college if st else None,
        "face_enrolled": st.face_enrolled if st else None,
        "activity_type_id": s.activity_type_id,
        "activity_name": s.activity_name,
        "description": s.description,
        "status": s.status.value if hasattr(s.status, "value") else str(s.status),
        "flag_reason": s.flag_reason,
        "started_at": s.started_at,
        "submitted_at": s.submitted_at,
        "expires_at": s.expires_at,
        "duration_hours": s.duration_hours,
        "in_time": in_time,
        "out_time": out_time,
        "activity_type": {
            "id": at.id,
            "name": at.name,
            "hours_per_unit": at.hours_per_unit,
            "points_per_unit": at.points_per_unit,
            "max_points": at.max_points,
            "target_lat": getattr(at, "target_lat", None),
            "target_lng": getattr(at, "target_lng", None),
            "radius_m": getattr(at, "radius_m", 500),
            "maps_url": getattr(at, "maps_url", None),
        }
        if at
        else None,
        "total_activity_points": points,
        "latest_face_matched": latest_fc.matched if latest_fc else None,
        "latest_face_reason": latest_fc.reason if latest_fc else None,
        "latest_face_processed_url": face_processed_url,
        "latest_face_check": {
            "id": latest_fc.id,
            "matched": latest_fc.matched,
            "cosine_score": latest_fc.cosine_score,
            "l2_score": latest_fc.l2_score,
            "total_faces": latest_fc.total_faces,
            "reason": latest_fc.reason,
            "processed_object": latest_fc.processed_object,
        }
        if latest_fc
        else None,
        "photos": photos_out,
        "location_trail": location_trail,
        "target_location": {
            "maps_url": getattr(at, "maps_url", None),
            "target_lat": getattr(at, "target_lat", None),
            "target_lng": getattr(at, "target_lng", None),
            "radius_m": int(getattr(at, "radius_m", 500) or 500),
        }
        if at
        else None,
    }


# ─────────────────────────────────────────────────────────────
# APPROVE / REJECT
# ─────────────────────────────────────────────────────────────

async def admin_approve_session(
    db: AsyncSession,
    session_id: int,
    *,
    current_admin_id: int | None = None,
) -> dict:
    res = await db.execute(
        select(ActivitySession)
        .where(ActivitySession.id == session_id)
        .with_for_update()
    )

    s = res.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    if s.status not in (
        ActivitySessionStatus.SUBMITTED,
        ActivitySessionStatus.FLAGGED,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve session in status {s.status}",
        )

    if getattr(s, "points_awarded_at", None) is not None:
        s.status = ActivitySessionStatus.APPROVED
        s.flag_reason = None

        await db.commit()
        await db.refresh(s)

        return {
            "id": s.id,
            "status": s.status.value,
            "points_awarded_now": 0,
            "reason": "Already awarded earlier",
        }

    s.status = ActivitySessionStatus.APPROVED
    s.flag_reason = None

    await db.flush()

    result = await award_points_for_session(
        db,
        s.id,
        created_by_admin_id=current_admin_id,
    )

    await db.commit()
    await db.refresh(s)

    return {
        "id": s.id,
        "status": s.status.value,
        "points_awarded_now": result.get("awarded", 0),
        "duration_minutes": result.get("duration_minutes"),
        "progress_total_minutes": result.get("total_minutes"),
        "points_awarded_total_for_activity": result.get("points_awarded_total_for_activity"),
        "student_total_points": result.get("student_total_points"),
    }


async def admin_reject_session(
    db: AsyncSession,
    session_id: int,
    reason: str,
) -> ActivitySession:
    s = await db.get(ActivitySession, session_id)

    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    if s.status not in (
        ActivitySessionStatus.SUBMITTED,
        ActivitySessionStatus.FLAGGED,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject session in status {s.status}",
        )

    s.status = ActivitySessionStatus.REJECTED
    s.flag_reason = reason

    await db.commit()
    await db.refresh(s)

    return s