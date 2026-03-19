# app/routes/events.py  ✅ FULL UPDATED (event photos + gps + clean fix)
from __future__ import annotations

from typing import List
from datetime import datetime, date as date_type, time as time_type
import math

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sql_delete

from app.core.database import get_db
from app.core.dependencies import get_current_student, get_current_admin
from app.core.activity_storage import upload_activity_image

from app.features.events.models import Event, EventSubmission, EventSubmissionPhoto
from app.features.events.models import EventActivityType

from app.features.events.schemas.events import (
    EventCreateIn,
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
            raise HTTPException(status_code=422, detail="event_date is required when end_time is a time value")
        return datetime.combine(event_date, end_val).replace(tzinfo=None)
    raise HTTPException(status_code=422, detail="Invalid end_time type")


def _event_out_dict(ev: Event) -> dict:
    end_val = getattr(ev, "end_time", None)
    end_time = end_val.time() if isinstance(end_val, datetime) else end_val

    return {
        "id": ev.id,
        "title": ev.title,
        "description": ev.description,
        "required_photos": ev.required_photos,
        "is_active": bool(getattr(ev, "is_active", True)),
        "event_date": ev.event_date,
        "start_time": ev.start_time,
        "end_time": end_time,
        "thumbnail_url": ev.thumbnail_url,
        "venue_name": getattr(ev, "venue_name", None),
        "maps_url": getattr(ev, "maps_url", None),

        # ✅ geofence fields
        "location_lat": getattr(ev, "location_lat", None),
        "location_lng": getattr(ev, "location_lng", None),
        "geo_radius_m": int(getattr(ev, "geo_radius_m", DEFAULT_EVENT_RADIUS_M) or DEFAULT_EVENT_RADIUS_M),
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

@router.post("/admin/events", response_model=EventOut)
async def admin_create_event_api(
    payload: EventCreateIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await create_event(db, payload)


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
    payload: EventCreateIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await update_event(db, event_id, payload)


@router.delete("/admin/events/{event_id}", status_code=204)
async def admin_delete_event_api(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    await delete_event(db, event_id)


@router.post("/admin/events/{event_id}/end", response_model=EventOut)
async def admin_end_event_api(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await end_event(db, event_id)


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


@router.get("/admin/events", response_model=list[EventOut])
async def admin_list_events_api(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    res = await db.execute(select(Event).order_by(Event.id.desc()))
    events = res.scalars().all()
    return [_event_out_dict(ev) for ev in events]


# =========================================================
# ---------------------- STUDENT ---------------------------
# =========================================================

@router.get("/student/events", response_model=list[EventOut])
async def student_events(
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await list_active_events(db)


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
    return _event_out_dict(ev)


@router.get("/student/events/{event_id}/certificates", response_model=list[StudentCertificateOut])
async def student_event_certificates(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await list_student_event_certificates(db=db, student_id=student.id, event_id=event_id)


@router.post("/student/events/{event_id}/register", response_model=RegisterOut)
async def register_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await register_for_event(db, student.id, event_id)


@router.get("/student/events/{event_id}/draft")
async def student_event_draft(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await get_student_event_draft_progress(db, student.id, event_id)


# ✅ event submission photos endpoint (uses EventSubmission/EventSubmissionPhoto)
@router.post("/student/events/submissions/{submission_id}/photos", response_model=PhotosUploadOut)
async def upload_photos(
    submission_id: int,
    start_seq: int = Query(..., description="Starting sequence number, e.g., 1"),

    # ✅ event-style fields
    images: List[UploadFile] | None = File(None, description="Upload multiple files with key 'images'"),
    lats: List[float] | None = Form(None, description="Latitude per image (same order as images)"),
    lngs: List[float] | None = Form(None, description="Longitude per image (same order as images)"),

    # ✅ legacy/mobile fallback fields
    image: UploadFile | None = File(None),
    file: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),

    lat: float | None = Form(None),
    lng: float | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),

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

    if sub.status != "in_progress":
        raise HTTPException(status_code=400, detail="Submission already completed")

    ev_res = await db.execute(select(Event).where(Event.id == sub.event_id))
    ev = ev_res.scalar_one_or_none()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    # ✅ enforce event time window before allowing uploads
    _ensure_event_window(ev)

    # ✅ normalize payload so both old and new app formats work
    normalized_images: list[UploadFile] = []
    normalized_lats: list[float | None] = []
    normalized_lngs: list[float | None] = []

    if images:
        normalized_images = list(images)
        normalized_lats = [float(x) if x is not None else None for x in (lats or [])]
        normalized_lngs = [float(x) if x is not None else None for x in (lngs or [])]
    else:
        single_upload = image or file or photo
        single_lat = lat if lat is not None else latitude
        single_lng = lng if lng is not None else longitude

        if single_upload is not None:
            normalized_images = [single_upload]
            normalized_lats = [float(single_lat)] if single_lat is not None else [None]
            normalized_lngs = [float(single_lng)] if single_lng is not None else [None]

    if not normalized_images:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "image file missing",
                "expected": {
                    "file_fields": ["images", "image", "file", "photo"],
                    "lat_fields": ["lats", "lat", "latitude"],
                    "lng_fields": ["lngs", "lng", "longitude"],
                },
            },
        )

    if len(normalized_lats) != len(normalized_images) or len(normalized_lngs) != len(normalized_images):
        raise HTTPException(
            status_code=422,
            detail="lats/lngs count must match number of uploaded images"
        )

    required_photos = int(getattr(ev, "required_photos", 3) or 3)
    if start_seq < 1 or start_seq > required_photos:
        raise HTTPException(status_code=400, detail=f"start_seq must be between 1 and {required_photos}")

    results: List[EventSubmissionPhoto] = []
    seq_no = start_seq

    target_lat = getattr(ev, "location_lat", None)
    target_lng = getattr(ev, "location_lng", None)
    radius_m = float(getattr(ev, "geo_radius_m", DEFAULT_EVENT_RADIUS_M) or DEFAULT_EVENT_RADIUS_M)

    # ✅ if geofence is enabled, GPS is mandatory
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

        dist = None
        in_geo = None
        if target_lat is not None and target_lng is not None and lat_val is not None and lng_val is not None:
            dist = _haversine_m(float(lat_val), float(lng_val), float(target_lat), float(target_lng))
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
            )
            db.add(photo_row)

        await db.commit()
        await db.refresh(photo_row)
        results.append(photo_row)
        seq_no += 1

    return PhotosUploadOut(submission_id=submission_id, photos=results)


@router.post("/student/submissions/{submission_id}/submit", response_model=SubmissionOut)
async def submit_event(
    submission_id: int,
    payload: FinalSubmitIn,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await final_submit(db, submission_id, student.id, payload.description)


# =========================================================
# ---------------------- ADMIN REVIEW ----------------------
# =========================================================

@router.get("/admin/events/{event_id}/submissions", response_model=list[AdminSubmissionOut])
async def admin_list_event_submissions(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await list_event_submissions(db, event_id)


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