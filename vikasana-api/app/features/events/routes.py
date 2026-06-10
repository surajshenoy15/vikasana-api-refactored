# app/routes/events.py
from __future__ import annotations
from pydantic import BaseModel
import os
import math
from datetime import datetime, date as date_type, time as time_type, timezone
from typing import List

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException, Form
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.features.events.role_guard import validate_event_role_access, can_student_view_event

from app.core.database import get_db
from app.core.dependencies import get_current_student, get_current_admin
from app.core.redis import cache_get, cache_set
from app.core.activity_storage import upload_activity_image
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import selectinload
from app.features.students.models import Student

from app.features.events.models import (
    Event,
    EventSubmission,
    EventSubmissionPhoto,
    EventRoleAssignment,
)
from app.features.events.schemas.events import (
    EventCreateIn,
    EventUpdateIn,
    EventOut,
    RegisterOut,
    PhotosUploadOut,
    FinalSubmitIn,
    SubmissionOut,
    AdminSubmissionOut,
    RejectIn,
)
from app.features.events.schemas.upload import ThumbnailUploadOut
from app.features.certificates.schemas.certificate import StudentCertificateOut
from app.features.activities.models import ActivityType
from app.features.events.service import (
    create_event,
    update_event,
    delete_event,
    list_active_events,
    register_for_event,
    final_submit,
    list_event_submissions,
    approve_submission,
    reject_submission,
    end_event,
    list_student_event_certificates,
    regenerate_event_certificates,
    auto_approve_event_from_sessions,
    get_student_event_draft_progress,
    _ensure_event_window,
    upload_event_thumbnail_file,
)
from app.core.minio_client import get_presigned_url
router = APIRouter(tags=["Events"])

DEFAULT_EVENT_RADIUS_M = 500


# =========================================================
# ---------------------- HELPERS --------------------------
# =========================================================

def _combine_event_datetime_ist_naive(event_date: date_type, t: time_type) -> datetime:
    return datetime.combine(event_date, t).replace(tzinfo=None)


def _as_naive_datetime_for_end_time(event_date: date_type | None, end_val):
    if end_val is None:
        return None
    if isinstance(end_val, datetime):
        return end_val.replace(tzinfo=None)
    if isinstance(end_val, time_type):
        if not event_date:
            raise HTTPException(
                status_code=422,
                detail="event_date is required when end_time is a time value",
            )
        return datetime.combine(event_date, end_val).replace(tzinfo=None)
    raise HTTPException(status_code=422, detail="Invalid end_time type")

def _event_out_dict(ev: Event) -> dict:
    end_val = getattr(ev, "end_time", None)
    end_time = end_val.time() if isinstance(end_val, datetime) else end_val

    mapping_rows = list(getattr(ev, "activity_types", []) or [])

    activity_type_ids = sorted(
        {
            int(row.activity_type_id)
            for row in mapping_rows
            if getattr(row, "activity_type_id", None) is not None
        }
    )

    scoring_rules = [
        {
            "activity_type_id": int(row.activity_type_id),
            "score_mode": getattr(row, "score_mode", "AUTO"),
            "manual_points": getattr(row, "manual_points", None),
            "min_required_hours": getattr(row, "min_required_hours", None),
        }
        for row in mapping_rows
        if getattr(row, "activity_type_id", None) is not None
    ]

    return {
        "id": ev.id,
        "title": ev.title,
        "description": ev.description,
        "required_photos": ev.required_photos,
        "is_active": bool(getattr(ev, "is_active", True)),
        "event_date": ev.event_date,
        "start_time": ev.start_time,
        "end_time": end_time,
        "thumbnail_url": getattr(ev, "thumbnail_url", None),
        "venue_name": getattr(ev, "venue_name", None),
        "maps_url": getattr(ev, "maps_url", None),
        "location_lat": getattr(ev, "location_lat", None),
        "location_lng": getattr(ev, "location_lng", None),
        "geo_radius_m": int(
            getattr(ev, "geo_radius_m", DEFAULT_EVENT_RADIUS_M) or DEFAULT_EVENT_RADIUS_M
        ),

        # ✅ NEW: volunteer/participant role protection fields
        "exclusive_group_key": getattr(ev, "exclusive_group_key", None),
        "event_role": getattr(ev, "event_role", None) or "PARTICIPANT",

        "activity_type_ids": activity_type_ids,
        "scoring_rules": scoring_rules,
    }
def _normalize_activity_type_ids(payload: EventCreateIn) -> list[int]:
    raw = getattr(payload, "activity_type_ids", None) or []

    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = [x.get("id") for x in raw]

    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]

    ids: list[int] = []
    if isinstance(raw, list):
        for x in raw:
            try:
                v = int(x)
                if v > 0:
                    ids.append(v)
            except Exception:
                pass

    return sorted(set(ids))


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# =========================================================
# ---------------------- ADMIN -----------------------------
# =========================================================

@router.get("/admin/events", response_model=list[EventOut])
async def admin_list_events_api(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    cache_key = "admin:events:list"

    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    res = await db.execute(select(Event).order_by(Event.id.desc()))
    events = res.scalars().all()
    result = [_event_out_dict(ev) for ev in events]

    await cache_set(cache_key, result, ttl=60)
    return result


@router.post("/admin/events", response_model=EventOut, status_code=201)
async def admin_create_event_api(
    payload: EventCreateIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    created = await create_event(db, payload)

    # optional cache bust by overwrite-shortening
    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)

    if isinstance(created, Event):
        return _event_out_dict(created)
    return created


@router.post("/admin/events/thumbnail-upload", response_model=ThumbnailUploadOut)
async def admin_event_thumbnail_upload(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
):
    return await upload_event_thumbnail_file(
        file=file,
        admin_id=admin.id,
    )

@router.put("/admin/events/{event_id}", response_model=EventOut)
async def admin_update_event_api(
    event_id: int,
    payload: EventUpdateIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    updated = await update_event(db, event_id, payload)

    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)

    if isinstance(updated, Event):
        return _event_out_dict(updated)
    return updated


@router.delete("/admin/events/{event_id}", status_code=204)
async def admin_delete_event_api(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    await delete_event(db, event_id)

    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)
    return None


@router.post("/admin/events/{event_id}/end", response_model=EventOut)
async def admin_end_event_api(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    ended = await end_event(db, event_id)

    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)

    if isinstance(ended, Event):
        return _event_out_dict(ended)
    return ended


@router.post("/admin/events/{event_id}/approve-and-issue")
async def admin_auto_approve_and_issue(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await auto_approve_event_from_sessions(db, event_id)


@router.post("/admin/events/{event_id}/certificates/regenerate")
async def admin_regenerate_event_certificates(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await regenerate_event_certificates(db, event_id)

PRIVATE_MEDIA_BUCKETS = {
    "face-verification",
    "activity-uploads",
    "vikasana-certificates",
}


@router.get("/admin/media/presign")
async def admin_presign_private_media(
    bucket: str = Query(...),
    object_key: str = Query(...),
    expires: int = Query(600, ge=60, le=3600),
    admin=Depends(get_current_admin),
):
    if bucket not in PRIVATE_MEDIA_BUCKETS:
        raise HTTPException(status_code=400, detail="Bucket is not allowed")

    object_key = (object_key or "").strip().lstrip("/")

    if not object_key or ".." in object_key or object_key.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid object key")

    try:
        url = get_presigned_url(
            bucket=bucket,
            object_name=object_key,
            expiry_seconds=expires,
            public=True,
        )

        return {
            "url": url,
            "expires_in_seconds": expires,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate private media URL: {str(e)}",
        )
    


class VolunteerAssignRequest(BaseModel):
    exclusive_group_key: str
    usns: list[str]


@router.post("/admin/events/assign-volunteers")
async def assign_volunteers(
    payload: VolunteerAssignRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    group_key = (payload.exclusive_group_key or "").strip()

    if not group_key:
        raise HTTPException(status_code=400, detail="exclusive_group_key is required")

    clean_usns = []
    for usn in payload.usns:
        if usn and usn.strip():
            clean_usns.append(usn.strip().upper())

    clean_usns = list(dict.fromkeys(clean_usns))

    if not clean_usns:
        raise HTTPException(status_code=400, detail="No USNs provided")

    students_result = await db.execute(
        select(Student).where(func.upper(Student.usn).in_(clean_usns))
    )
    students = students_result.scalars().all()

    found_usns = {(s.usn or "").upper() for s in students}
    missing_usns = [u for u in clean_usns if u not in found_usns]

    created = 0
    updated = 0

    for student in students:
        existing_result = await db.execute(
            select(EventRoleAssignment).where(
                EventRoleAssignment.student_id == student.id,
                EventRoleAssignment.exclusive_group_key == group_key,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            existing.role_allowed = "VOLUNTEER"
            updated += 1
        else:
            db.add(
                EventRoleAssignment(
                    student_id=student.id,
                    exclusive_group_key=group_key,
                    role_allowed="VOLUNTEER",
                )
            )
            created += 1

    await db.commit()

    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)

    return {
        "message": "Volunteer assignments saved",
        "exclusive_group_key": group_key,
        "total_input": len(clean_usns),
        "created": created,
        "updated": updated,
        "missing_usns": missing_usns,
    }


@router.get("/admin/events/role-assignments")
async def list_role_assignments(
    exclusive_group_key: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    group_key = (exclusive_group_key or "").strip()

    if not group_key:
        raise HTTPException(status_code=400, detail="exclusive_group_key is required")

    result = await db.execute(
        select(
            EventRoleAssignment.id.label("assignment_id"),
            EventRoleAssignment.exclusive_group_key,
            EventRoleAssignment.role_allowed,
            EventRoleAssignment.created_at,
            Student.id.label("student_id"),
            Student.name,
            Student.usn,
            Student.email,
        )
        .join(Student, Student.id == EventRoleAssignment.student_id)
        .where(EventRoleAssignment.exclusive_group_key == group_key)
        .order_by(Student.usn)
    )

    rows = result.mappings().all()

    return [
        {
            "assignment_id": row["assignment_id"],
            "exclusive_group_key": row["exclusive_group_key"],
            "role_allowed": row["role_allowed"],
            "created_at": row["created_at"],
            "student_id": row["student_id"],
            "name": row["name"],
            "usn": row["usn"],
            "email": row["email"],
        }
        for row in rows
    ]


@router.delete("/admin/events/role-assignments/{assignment_id}")
async def delete_role_assignment(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    result = await db.execute(
        select(EventRoleAssignment).where(EventRoleAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()

    if not assignment:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    await db.delete(assignment)
    await db.commit()

    await cache_set("admin:events:list", None, ttl=1)
    await cache_set("student:events:list", None, ttl=1)

    return {
        "message": "Volunteer assignment removed",
        "assignment_id": assignment_id,
    }
# =========================================================
# ---------------------- STUDENT ---------------------------
# =========================================================

@router.get("/student/events", response_model=list[EventOut])
async def student_events(
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    events = await list_active_events(db)

    # ✅ NEW: hide wrong-role events
    # - Volunteers see only VOLUNTEER event for the same group
    # - Normal students see only PARTICIPANT event
    visible_events = []

    for ev in events:
        allowed = await can_student_view_event(
            db=db,
            student_id=student.id,
            event=ev,
        )

        if allowed:
            visible_events.append(ev)

    events = visible_events

    event_ids = [ev.id for ev in events]

    # registered counts
    registered_count_map: dict[int, int] = {}
    if event_ids:
        count_rows = await db.execute(
            select(
                EventSubmission.event_id,
                func.count(func.distinct(EventSubmission.student_id)).label("registered_count"),
            )
            .where(EventSubmission.event_id.in_(event_ids))
            .group_by(EventSubmission.event_id)
        )
        registered_count_map = {int(eid): int(c or 0) for eid, c in count_rows.all()}

    # ── Load ActivityType caps/rates for AUTO points ──
    at_map: dict[int, ActivityType] = {}
    all_at_ids: set[int] = set()
    items: list[dict] = []

    for ev in events:
        it = _event_out_dict(ev)
        for r in it.get("scoring_rules", []):
            if r.get("activity_type_id") is not None:
                all_at_ids.add(int(r["activity_type_id"]))
        items.append(it)

    if all_at_ids:
        at_rows = await db.execute(select(ActivityType).where(ActivityType.id.in_(all_at_ids)))
        at_map = {int(a.id): a for a in at_rows.scalars().all()}

    def _points_for(item: dict) -> dict:
        rules = item.get("scoring_rules", []) or []
        manual_total = 0
        auto_max_total = 0
        has_manual = False
        has_auto = False
        min_hours = 0.0

        for r in rules:
            mode = str(r.get("score_mode") or "AUTO").upper()
            mh = float(r.get("min_required_hours") or 0)
            min_hours = max(min_hours, mh)

            if mode == "MANUAL":
                has_manual = True
                manual_total += int(r.get("manual_points") or 0)
            else:
                has_auto = True
                at = at_map.get(int(r["activity_type_id"])) if r.get("activity_type_id") else None
                mp = int(getattr(at, "max_points", 0) or 0) if at else 0
                auto_max_total += mp

        if has_manual and has_auto:
            mode = "mixed"
        elif has_manual:
            mode = "fixed"
        elif has_auto:
            mode = "auto"
        else:
            mode = "none"

        if mode == "fixed":
            display = f"+{manual_total} pts"
        elif mode == "auto":
            display = f"Up to {auto_max_total} pts" if auto_max_total > 0 else "Based on hours"
        elif mode == "mixed":
            display = f"+{manual_total} pts"
            display += f" · up to +{auto_max_total}" if auto_max_total > 0 else " + hours-based"
        else:
            display = "—"

        return {
            "points_mode": mode,
            "manual_points_total": manual_total,
            "auto_max_total": auto_max_total,
            "max_points": manual_total + auto_max_total,
            "min_required_hours": min_hours,
            "points_display": display,
            "points": manual_total if mode == "fixed" else 0,
        }

    result = []

    for ev, item in zip(events, items):
        item["registered_count"] = registered_count_map.get(ev.id, 0)
        item["max_participants"] = getattr(ev, "max_participants", None) or 100
        item["capacity"] = item.get("max_participants") or 100
        item.update(_points_for(item))
        result.append(item)

    return result
@router.get("/student/events/{event_id}", response_model=EventOut)
async def student_event_detail(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    res = await db.execute(select(Event).where(Event.id == event_id))
    ev = res.scalar_one_or_none()

    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    await validate_event_role_access(
        db=db,
        student_id=student.id,
        event_id=event_id,
    )

    return _event_out_dict(ev)


@router.get(
    "/student/events/{event_id}/certificates",
    response_model=list[StudentCertificateOut],
)
async def student_event_certificates(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await list_student_event_certificates(
        db=db,
        student_id=student.id,
        event_id=event_id,
    )


@router.post("/student/events/{event_id}/register", response_model=RegisterOut)
async def register_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    await validate_event_role_access(
        db=db,
        student_id=student.id,
        event_id=event_id,
    )

    return await register_for_event(db, student.id, event_id)


@router.get("/student/events/{event_id}/draft")
async def student_event_draft(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    await validate_event_role_access(
        db=db,
        student_id=student.id,
        event_id=event_id,
    )

    return await get_student_event_draft_progress(db, student.id, event_id)
@router.post("/student/events/submissions/{submission_id}/photos", response_model=PhotosUploadOut)
async def upload_photos(
    submission_id: int,
    start_seq: int = Query(..., description="Starting sequence number, e.g., 1"),

    # event-style fields
    images: List[UploadFile] | None = File(None, description="Upload multiple files with key 'images'"),
    lats: List[float] | None = Form(None, description="Latitude per image (same order as images)"),
    lngs: List[float] | None = Form(None, description="Longitude per image (same order as images)"),
    captured_ats: List[str] | None = Form(None, description="Captured timestamp per image"),

    # legacy/mobile fallback fields
    image: UploadFile | None = File(None),
    file: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),

    lat: float | None = Form(None),
    lng: float | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    captured_at: str | None = Form(None),

    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    sub_res = await db.execute(
        select(EventSubmission).where(
            EventSubmission.id == submission_id,
            EventSubmission.student_id == student.id,
        )
    )
    sub = sub_res.scalar_one_or_none()

    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found for this student")

    # ✅ NEW: prevent wrong-role student from uploading photos
    # Example:
    # - Volunteer cannot upload photos for participant event
    # - Normal student cannot upload photos for volunteer event
    # - Student cannot continue another role in same exclusive_group_key
    await validate_event_role_access(
        db=db,
        student_id=student.id,
        event_id=sub.event_id,
    )

    if sub.status != "in_progress":
        raise HTTPException(status_code=400, detail="Submission already completed")

    ev_res = await db.execute(select(Event).where(Event.id == sub.event_id))
    ev = ev_res.scalar_one_or_none()

    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    _ensure_event_window(ev)

    normalized_images: list[UploadFile] = []
    normalized_lats: list[float | None] = []
    normalized_lngs: list[float | None] = []
    normalized_captured_ats: list[datetime | None] = []

    def parse_captured_at(value: str | None) -> datetime | None:
        if not value:
            return None

        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    if images:
        normalized_images = list(images)
        normalized_lats = [float(x) if x is not None else None for x in (lats or [])]
        normalized_lngs = [float(x) if x is not None else None for x in (lngs or [])]
        normalized_captured_ats = [parse_captured_at(x) for x in (captured_ats or [])]
    else:
        single_upload = image or file or photo
        single_lat = lat if lat is not None else latitude
        single_lng = lng if lng is not None else longitude

        if single_upload is not None:
            normalized_images = [single_upload]
            normalized_lats = [float(single_lat)] if single_lat is not None else [None]
            normalized_lngs = [float(single_lng)] if single_lng is not None else [None]
            normalized_captured_ats = [parse_captured_at(captured_at)]

    if not normalized_images:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "image file missing",
                "expected": {
                    "file_fields": ["images", "image", "file", "photo"],
                    "lat_fields": ["lats", "lat", "latitude"],
                    "lng_fields": ["lngs", "lng", "longitude"],
                    "timestamp_fields": ["captured_ats", "captured_at"],
                },
            },
        )

    if len(normalized_lats) != len(normalized_images) or len(normalized_lngs) != len(normalized_images):
        raise HTTPException(
            status_code=422,
            detail="lats/lngs count must match number of uploaded images",
        )

    # If timestamp is missing, fallback to server current time
    if len(normalized_captured_ats) < len(normalized_images):
        normalized_captured_ats += [None] * (len(normalized_images) - len(normalized_captured_ats))

    required_photos = int(getattr(ev, "required_photos", 3) or 3)

    if start_seq < 1 or start_seq > required_photos:
        raise HTTPException(
            status_code=400,
            detail=f"start_seq must be between 1 and {required_photos}",
        )

    results: List[EventSubmissionPhoto] = []
    seq_no = start_seq

    target_lat = getattr(ev, "location_lat", None)
    target_lng = getattr(ev, "location_lng", None)
    radius_m = float(getattr(ev, "geo_radius_m", DEFAULT_EVENT_RADIUS_M) or DEFAULT_EVENT_RADIUS_M)

    if target_lat is not None and target_lng is not None:
        for i in range(len(normalized_images)):
            if normalized_lats[i] is None or normalized_lngs[i] is None:
                raise HTTPException(
                    status_code=422,
                    detail="GPS latitude and longitude are required for this event",
                )

    for idx, img in enumerate(normalized_images):
        if seq_no > required_photos:
            break

        file_bytes = await img.read()

        if not file_bytes:
            seq_no += 1
            continue

        image_url = await upload_activity_image(
            file_bytes=file_bytes,
            content_type=img.content_type or "application/octet-stream",
            filename=img.filename or f"event_{submission_id}_{seq_no}.jpg",
            student_id=student.id,
            session_id=submission_id,
        )

        lat_val = normalized_lats[idx]
        lng_val = normalized_lngs[idx]
        captured_at_val = normalized_captured_ats[idx] or datetime.now(timezone.utc)

        dist = None
        in_geo = None

        if target_lat is not None and target_lng is not None and lat_val is not None and lng_val is not None:
            dist = _haversine_m(
                float(lat_val),
                float(lng_val),
                float(target_lat),
                float(target_lng),
            )
            in_geo = dist <= radius_m

        photo_res = await db.execute(
            select(EventSubmissionPhoto).where(
                EventSubmissionPhoto.submission_id == submission_id,
                EventSubmissionPhoto.seq_no == seq_no,
            )
        )
        existing = photo_res.scalar_one_or_none()

        if existing:
            existing.image_url = image_url
            existing.lat = float(lat_val) if lat_val is not None else None
            existing.lng = float(lng_val) if lng_val is not None else None
            existing.distance_m = float(dist) if dist is not None else None
            existing.is_in_geofence = bool(in_geo) if in_geo is not None else None
            existing.captured_at = captured_at_val
            photo_row = existing
        else:
            photo_row = EventSubmissionPhoto(
                submission_id=submission_id,
                seq_no=seq_no,
                image_url=image_url,
                lat=float(lat_val) if lat_val is not None else None,
                lng=float(lng_val) if lng_val is not None else None,
                distance_m=float(dist) if dist is not None else None,
                is_in_geofence=bool(in_geo) if in_geo is not None else None,
                captured_at=captured_at_val,
            )
            db.add(photo_row)

        await db.commit()
        await db.refresh(photo_row)

        results.append(photo_row)
        seq_no += 1

    return PhotosUploadOut(
        submission_id=submission_id,
        photos=results,
    )


@router.post("/student/submissions/{submission_id}/submit", response_model=SubmissionOut)
async def submit_event(
    submission_id: int,
    payload: FinalSubmitIn,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    sub_res = await db.execute(
        select(EventSubmission).where(
            EventSubmission.id == submission_id,
            EventSubmission.student_id == student.id,
        )
    )
    sub = sub_res.scalar_one_or_none()

    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found for this student")

    await validate_event_role_access(
        db=db,
        student_id=student.id,
        event_id=sub.event_id,
    )

    return await final_submit(db, submission_id, student.id, payload.description)


# =========================================================
# ---------------------- ADMIN REVIEW ----------------------
# =========================================================
@router.get("/admin/events/{event_id}/submissions")
async def admin_list_event_submissions(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    stmt = (
        select(
            EventSubmission.id.label("submission_id"),
            EventSubmission.event_id.label("event_id"),
            EventSubmission.student_id.label("student_id"),

            Student.name.label("student_name"),
            Student.usn.label("student_usn"),
            Student.college.label("college"),

            EventSubmission.status.label("status"),
            EventSubmission.submitted_at.label("submitted_at"),
            EventSubmission.created_at.label("created_at"),
            
            EventSubmission.description.label("description"),
            EventSubmission.awarded_points.label("points_awarded"),

            func.count(EventSubmissionPhoto.id).label("photo_count"),
        )
        .outerjoin(Student, Student.id == EventSubmission.student_id)
        .outerjoin(
            EventSubmissionPhoto,
            EventSubmissionPhoto.submission_id == EventSubmission.id,
        )
        .where(EventSubmission.event_id == event_id)
        .group_by(
            EventSubmission.id,
            EventSubmission.event_id,
            EventSubmission.student_id,
            Student.name,
            Student.usn,
            Student.college,
            EventSubmission.status,
            EventSubmission.submitted_at,
            EventSubmission.created_at,
            
            EventSubmission.description,
            EventSubmission.awarded_points,
        )
        .order_by(EventSubmission.id.desc())
    )

    rows = (await db.execute(stmt)).all()

    safe_rows = []

    for row in rows:
        raw_status = row.status
        status = raw_status.value if hasattr(raw_status, "value") else str(raw_status or "")

        safe_rows.append({
            "id": row.submission_id,
            "submission_id": row.submission_id,
            "event_id": row.event_id,
            "student_id": row.student_id,

            "student_name": row.student_name or "",
            "name": row.student_name or "",

            "student_usn": row.student_usn or "",
            "usn": row.student_usn or "",

            "college": row.college or "",
            "college_name": row.college or "",

            "status": status,
            "submitted_at": row.submitted_at,
            "created_at": row.created_at,
            "updated_at": None,

            "description": row.description or "",
            "points_awarded": int(row.points_awarded or 0),
            "photo_count": int(row.photo_count or 0),
        })

    return JSONResponse(
        content=jsonable_encoder(safe_rows),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
        },
    )
@router.get("/admin/submissions/{submission_id}/photos")
async def admin_get_submission_photos(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    sub_res = await db.execute(
        select(EventSubmission).where(EventSubmission.id == submission_id)
    )
    submission = sub_res.scalar_one_or_none()

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    photo_res = await db.execute(
        select(EventSubmissionPhoto)
        .where(EventSubmissionPhoto.submission_id == submission_id)
        .order_by(EventSubmissionPhoto.seq_no.asc())
    )

    photos = photo_res.scalars().all()

    # ✅ Use real bucket from env, not hardcoded old bucket
    activity_bucket = (
        os.getenv("AWS_S3_BUCKET_ACTIVITIES")
        or os.getenv("MINIO_BUCKET_ACTIVITIES")
        or "activity-uploads-652197206453-ap-south-1-an"
    )

    result = []

    for photo in photos:
        raw_url = getattr(photo, "image_url", "") or ""
        object_key = raw_url.strip().split("?")[0].lstrip("/")

        # If DB stored full URL, extract path
        if object_key.startswith("http://") or object_key.startswith("https://"):
            try:
                from urllib.parse import urlparse

                parsed = urlparse(object_key)
                object_key = parsed.path.lstrip("/")
            except Exception:
                pass

        # ✅ Remove possible bucket/proxy prefixes
        prefixes_to_remove = [
            f"{activity_bucket}/",
            "activity-uploads/",
            "activity-uploads-652197206453-ap-south-1-an/",
            "minio/activity-uploads/",
            "minio/activity-uploads-652197206453-ap-south-1-an/",
        ]

        for prefix in prefixes_to_remove:
            if object_key.startswith(prefix):
                object_key = object_key[len(prefix):]
                break

        if not object_key:
            continue

        signed_url = get_presigned_url(
            bucket=activity_bucket,
            object_name=object_key,
            expiry_seconds=60,
            public=True,
        )

        result.append({
            "id": getattr(photo, "id", None),
            "seq_no": getattr(photo, "seq_no", None),
            "url": signed_url,
        })

    response = JSONResponse({
        "submission_id": submission_id,
        "expires_in_seconds": 60,
        "photos": result,
    })

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"

    return response

@router.post("/admin/submissions/{submission_id}/approve", response_model=AdminSubmissionOut)
async def approve_event_submission_api(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await approve_submission(db, submission_id)


@router.post("/admin/submissions/{submission_id}/reject", response_model=AdminSubmissionOut)
async def reject_event_submission_api(
    submission_id: int,
    payload: RejectIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await reject_submission(db, submission_id, payload.reason)