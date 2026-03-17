# app/routes/activity.py

from __future__ import annotations

from typing import Optional
import base64

from pydantic import BaseModel, Field
from fastapi import (
    APIRouter,
    Depends,
    UploadFile,
    File,
    Query,
    HTTPException,
    Form,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.dependencies import get_current_student, get_current_admin

from app.features.activities.schemas.activity import (
    ActivityTypeOut,
    RequestActivityTypeIn,
    CreateSessionIn,
    PhotoOut,
    SubmitSessionOut,
    SessionListItemOut,
    SessionDetailOut,
)

from app.features.activities.service import (
    list_activity_types,
    request_new_activity_type,
    create_session,
    add_photo_to_session,
    submit_session,
    list_student_sessions,
    get_student_session_detail,
)

from app.core.activity_storage import upload_activity_image
from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType
from app.features.events.models import Event

# ✅ Face check models + services
from app.features.activities.models import ActivityFaceCheck
from app.features.face.models import StudentFaceEmbedding
from app.features.face.service import match_in_group


router = APIRouter(prefix="/student/activity", tags=["Student - Activity"])
admin_router = APIRouter(prefix="/admin/activity", tags=["Admin - Activity"])
legacy_router = APIRouter(prefix="/student", tags=["Student - Legacy"])

AUTO_APPROVE_MIN_MATCHES = 1
MAX_PHOTOS_FOR_AUTO = 5


# ─────────────────────────────────────────────────────────────
# Datetime parsing helper
# ─────────────────────────────────────────────────────────────
def _normalize_ddmmyyyy_date(date_part: str) -> str:
    parts = date_part.strip().split("/")
    if len(parts) != 3:
        return date_part.strip()
    d, m, y = parts
    return f"{d.zfill(2)}/{m.zfill(2)}/{y}"


def parse_captured_at(meta_captured_at: str):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    s = (meta_captured_at or "").strip()
    if not s:
        raise HTTPException(status_code=422, detail="meta_captured_at is required")

    # 1) Try ISO (supports Z)
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        return dt
    except ValueError:
        pass

    cleaned = s
    cleaned = cleaned.replace(" AM", " am").replace(" PM", " pm")
    cleaned = cleaned.replace(" a.m.", " am").replace(" p.m.", " pm")
    cleaned = cleaned.strip()

    if "," in cleaned:
        date_part, time_part = [x.strip() for x in cleaned.split(",", 1)]
    else:
        parts = cleaned.split()
        if len(parts) >= 2:
            date_part = parts[0].strip()
            time_part = " ".join(parts[1:]).strip()
        else:
            date_part, time_part = cleaned, ""

    date_part_norm = _normalize_ddmmyyyy_date(date_part)

    candidates = [
        f"{date_part_norm}, {time_part}".strip(),
        f"{date_part_norm} {time_part}".strip(),
        cleaned,
    ]

    fmts = [
        "%d/%m/%Y, %I:%M:%S %p",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y, %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y, %I:%M:%S %p",
        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y, %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
    ]

    last_err = None
    for cand in candidates:
        if not cand:
            continue
        cand2 = cand.replace(" am", " AM").replace(" pm", " PM")
        for fmt in fmts:
            try:
                dt = datetime.strptime(cand2, fmt)
                dt = dt.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                return dt
            except ValueError as e:
                last_err = e

    raise HTTPException(
        status_code=422,
        detail={
            "message": "Invalid meta_captured_at format",
            "received": s,
            "error": str(last_err) if last_err else "unparseable",
        },
    )


# ─────────────────────────────────────────────────────────────
# seq_no helper
# ─────────────────────────────────────────────────────────────
async def _next_seq_no(db: AsyncSession, session_id: int) -> int:
    q = select(func.max(ActivityPhoto.seq_no)).where(ActivityPhoto.session_id == session_id)
    res = await db.execute(q)
    mx = res.scalar_one_or_none()
    return int(mx or 0) + 1


# ─────────────────────────────────────────────────────────────
# session uploadable helper
# ─────────────────────────────────────────────────────────────
async def _assert_session_uploadable(db: AsyncSession, student_id: int, session_id: int):
    res = await db.execute(
        select(ActivitySession).where(
            ActivitySession.id == session_id,
            ActivitySession.student_id == student_id,
        )
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # ✅ Allow upload in DRAFT and FLAGGED
    if session.status not in (ActivitySessionStatus.DRAFT, ActivitySessionStatus.FLAGGED):
        raise HTTPException(status_code=400, detail="Cannot upload photos in current session status")

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    if session.expires_at and now > session.expires_at:
        session.status = ActivitySessionStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=400, detail="Session expired")


# ─────────────────────────────────────────────────────────────
# Face check upsert (INLINE to avoid missing controller import)
# ─────────────────────────────────────────────────────────────
async def upsert_face_check(
    db: AsyncSession,
    *,
    session_id: int,
    photo_id: int,
    matched: bool,
    cosine_score: Optional[float] = None,
    l2_score: Optional[float] = None,
    total_faces: Optional[int] = None,
    processed_object: Optional[str] = None,
    reason: Optional[str] = None,
) -> ActivityFaceCheck:
    photo = await db.get(ActivityPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="ActivityPhoto not found")

    if photo.student_id is None:
        raise HTTPException(status_code=400, detail="ActivityPhoto.student_id is NULL; cannot create face check")

    session = await db.get(ActivitySession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ActivitySession not found")

    if photo.session_id != session_id:
        raise HTTPException(status_code=400, detail="photo_id does not belong to session_id")

    if session.student_id != photo.student_id:
        raise HTTPException(status_code=400, detail="session and photo belong to different students")

    student_id = photo.student_id

    existing = await db.execute(
        select(ActivityFaceCheck).where(
            ActivityFaceCheck.session_id == session_id,
            ActivityFaceCheck.photo_id == photo_id,
        )
    )
    face_check = existing.scalar_one_or_none()

    if face_check:
        face_check.student_id = student_id
        face_check.matched = matched
        face_check.cosine_score = cosine_score
        face_check.l2_score = l2_score
        face_check.total_faces = total_faces
        face_check.processed_object = processed_object
        face_check.reason = reason
        await db.commit()
        await db.refresh(face_check)
        return face_check

    face_check = ActivityFaceCheck(
        student_id=student_id,
        session_id=session_id,
        photo_id=photo_id,
        matched=matched,
        cosine_score=cosine_score,
        l2_score=l2_score,
        total_faces=total_faces,
        processed_object=processed_object,
        reason=reason,
    )
    db.add(face_check)
    await db.commit()
    await db.refresh(face_check)
    return face_check


# ─────────────────────────────────────────────────────────────
# Face helpers
# ─────────────────────────────────────────────────────────────
async def _get_student_embedding(db: AsyncSession, student_id: int) -> list[float]:
    r = await db.execute(select(StudentFaceEmbedding).where(StudentFaceEmbedding.student_id == student_id))
    row = r.scalar_one_or_none()
    if not row:
        return []
    try:
        return row.get_embedding() or []
    except Exception:
        return []


async def _count_face_matches(db: AsyncSession, session_id: int) -> int:
    r = await db.execute(
        select(func.count(ActivityFaceCheck.id)).where(
            ActivityFaceCheck.session_id == session_id,
            ActivityFaceCheck.matched == True,
        )
    )
    return int(r.scalar_one() or 0)


async def _maybe_auto_approve(db: AsyncSession, session_id: int) -> dict:
    session = await db.get(ActivitySession, session_id)
    if not session:
        return {"auto_approved": False, "matched_count": 0}

    if session.status not in (ActivitySessionStatus.SUBMITTED, ActivitySessionStatus.FLAGGED):
        matched_count = await _count_face_matches(db, session_id)
        return {"auto_approved": False, "matched_count": matched_count}

    matched_count = await _count_face_matches(db, session_id)

    if matched_count >= AUTO_APPROVE_MIN_MATCHES:
        session.status = ActivitySessionStatus.APPROVED
        session.flag_reason = None
        await db.commit()
        await db.refresh(session)
        return {"auto_approved": True, "matched_count": matched_count}

    session.status = ActivitySessionStatus.FLAGGED
    session.flag_reason = f"Face not verified: {matched_count}/{AUTO_APPROVE_MIN_MATCHES} matches"
    await db.commit()
    await db.refresh(session)
    return {"auto_approved": False, "matched_count": matched_count}


# ─────────────────────────────────────────────────────────────
# Photo replacement helper (fix unique constraint on seq_no)
# ─────────────────────────────────────────────────────────────
async def _delete_existing_photo_if_any(db: AsyncSession, session_id: int, seq_no: int):
    r = await db.execute(
        select(ActivityPhoto).where(
            ActivityPhoto.session_id == session_id,
            ActivityPhoto.seq_no == seq_no,
        )
    )
    old = r.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.commit()


# ─────────────────────────────────────────────────────────────
# Student routes
# ─────────────────────────────────────────────────────────────
@router.get("/types", response_model=list[ActivityTypeOut])
async def get_types(db: AsyncSession = Depends(get_db)):
    return await list_activity_types(db, include_pending=False)


@router.post("/types/request", response_model=ActivityTypeOut)
async def request_type(
    payload: RequestActivityTypeIn,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await request_new_activity_type(db, payload.name, payload.description)


@router.post("/sessions")
async def create_activity_session(
    payload: CreateSessionIn,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    s = await create_session(
        db,
        student.id,
        payload.activity_type_id,
        payload.activity_name,
        payload.description,
    )
    return {"success": True, "session_id": s.id, "id": s.id, "status": getattr(s, "status", None), "session": s}


@router.post("/sessions/from-event")
async def create_activity_session_from_event(
    event_id: int = Query(..., ge=1),
    description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    r = await db.execute(select(Event).where(Event.id == event_id, Event.is_active == True))
    ev = r.scalar_one_or_none()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found or inactive")

    EVENT_ACTIVITY_TYPE_ID = 6
    s = await create_session(db, student.id, EVENT_ACTIVITY_TYPE_ID, ev.title, description)
    return {"success": True, "session_id": s.id, "id": s.id, "status": getattr(s, "status", None)}


async def _handle_photo_upload_and_save(
    *,
    db: AsyncSession,
    student_id: int,
    session_id: int,
    meta_captured_at: str,
    lat: float,
    lng: float,
    sha256: str | None,
    image: UploadFile,
    seq_no: int | None,
) -> PhotoOut:
    await _assert_session_uploadable(db, student_id, session_id)

    file_bytes = await image.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    image_url = await upload_activity_image(
        file_bytes=file_bytes,
        content_type=image.content_type or "application/octet-stream",
        filename=image.filename or "photo.jpg",
        student_id=student_id,
        session_id=session_id,
    )

    captured_at = parse_captured_at(meta_captured_at)

    if seq_no is None:
        seq_no = await _next_seq_no(db, session_id)

    seq_no_int = int(seq_no)

    # ✅ Replace old photo if same seq_no exists
    await _delete_existing_photo_if_any(db, session_id, seq_no_int)

    photo = await add_photo_to_session(
        db=db,
        student_id=student_id,
        session_id=session_id,
        seq_no=seq_no_int,
        image_url=image_url,
        captured_at=captured_at,
        lat=lat,
        lng=lng,
        sha256=sha256,
    )

    # ✅ Face check best-effort
    try:
        stored_embedding = await _get_student_embedding(db, student_id)
        if stored_embedding:
            img_b64 = base64.b64encode(file_bytes).decode("utf-8")
            result = match_in_group(img_b64, stored_embedding)

            await upsert_face_check(
                db,
                session_id=session_id,
                photo_id=photo.id,
                matched=bool(result.get("matched")),
                cosine_score=result.get("cosine_score"),
                l2_score=result.get("l2_score"),
                total_faces=result.get("total_faces"),
                processed_object="activity_photo_upload",
                reason=result.get("reason"),
            )

            # if already submitted/flagged, try auto approve immediately
            await _maybe_auto_approve(db, session_id)
    except Exception:
        pass

    return photo


@router.post("/sessions/{session_id}/photos", response_model=PhotoOut)
@router.post("/sessions/{session_id}/photos/", response_model=PhotoOut)
async def upload_activity_photo(
    session_id: int,
    seq_no: int | None = Query(None, ge=1),
    meta_captured_at: str = Query(...),
    lat: float = Query(...),
    lng: float = Query(...),
    sha256: str | None = Query(None),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await _handle_photo_upload_and_save(
        db=db,
        student_id=student.id,
        session_id=session_id,
        meta_captured_at=meta_captured_at,
        lat=lat,
        lng=lng,
        sha256=sha256,
        image=image,
        seq_no=seq_no,
    )


@router.post("/sessions/{session_id}/resubmit")
async def resubmit_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    s = await db.get(ActivitySession, session_id)
    if not s or s.student_id != student.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if s.status != ActivitySessionStatus.FLAGGED:
        raise HTTPException(status_code=400, detail="Only FLAGGED sessions can be resubmitted")

    s.status = ActivitySessionStatus.DRAFT
    s.flag_reason = None
    s.submitted_at = None

    await db.commit()
    await db.refresh(s)

    return {"success": True, "session_id": s.id, "status": s.status}


@router.post("/sessions/{session_id}/submit", response_model=SubmitSessionOut)
async def submit_activity(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    session, newly, total_points, total_hours = await submit_session(db, student.id, session_id)

    face_auto = await _maybe_auto_approve(db, session_id)

    session = await db.get(ActivitySession, session_id)

    return {
        "session": session,
        "newly_awarded_points": newly,
        "total_points_for_type": total_points,
        "total_hours_for_type": total_hours,
        "face_auto": face_auto,
    }


@router.get("/sessions", response_model=list[SessionListItemOut])
async def my_sessions(
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await list_student_sessions(db, student.id)


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await get_student_session_detail(db, student.id, session_id)


# ─────────────────────────────────────────────────────────────
# Admin - Activity
# ─────────────────────────────────────────────────────────────
@admin_router.get("/types", response_model=list[ActivityTypeOut])
async def admin_list_types(
    include_pending: bool = True,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await list_activity_types(db, include_pending=include_pending)


class AdminUpdateActivityTypeGeoIn(BaseModel):
    maps_url: Optional[str] = None
    target_lat: Optional[float] = None
    target_lng: Optional[float] = None
    radius_m: Optional[int] = Field(default=500, ge=50, le=5000)


@admin_router.put("/types/{type_id}", response_model=ActivityTypeOut)
async def admin_update_activity_type_geo(
    type_id: int,
    payload: AdminUpdateActivityTypeGeoIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    res = await db.execute(select(ActivityType).where(ActivityType.id == type_id))
    at = res.scalar_one_or_none()
    if not at:
        raise HTTPException(status_code=404, detail="Activity type not found")

    at.maps_url = payload.maps_url
    at.target_lat = payload.target_lat
    at.target_lng = payload.target_lng
    at.radius_m = int(payload.radius_m or 500)

    await db.commit()
    await db.refresh(at)
    return at


# ─────────────────────────────────────────────────────────────
# Legacy compatibility route
# ─────────────────────────────────────────────────────────────
@legacy_router.post("/submissions/{submission_id}/photos", response_model=PhotoOut)
@legacy_router.post("/submissions/{submission_id}/photos/", response_model=PhotoOut)
async def legacy_upload_submission_photo(
    submission_id: int,
    start_seq: int = Query(1, ge=1),
    meta_captured_at: str | None = Query(None),
    captured_at: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    latitude: float | None = Query(None),
    longitude: float | None = Query(None),
    sha256: str | None = Query(None),
    meta_captured_at_f: str | None = Form(None),
    captured_at_f: str | None = Form(None),
    lat_f: float | None = Form(None),
    lng_f: float | None = Form(None),
    latitude_f: float | None = Form(None),
    longitude_f: float | None = Form(None),
    sha256_f: str | None = Form(None),
    image: UploadFile | None = File(None),
    file: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    cap = meta_captured_at or captured_at or meta_captured_at_f or captured_at_f

    lat_val = (
        lat
        if lat is not None
        else (latitude if latitude is not None else (lat_f if lat_f is not None else latitude_f))
    )
    lng_val = (
        lng
        if lng is not None
        else (longitude if longitude is not None else (lng_f if lng_f is not None else longitude_f))
    )

    sha = sha256 or sha256_f
    upload = image or file or photo

    if upload is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "image file missing. Send multipart/form-data with one of: image | file | photo",
                "expected": {"file_field": ["image", "file", "photo"]},
            },
        )

    if not cap:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "captured_at missing. Send one of: meta_captured_at | captured_at (query or form)",
                "expected": {"captured_at_field": ["meta_captured_at", "captured_at"]},
            },
        )

    if lat_val is None or lng_val is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "lat/lng missing. Send lat,lng or latitude,longitude (query or form)",
                "received": {"lat": lat_val, "lng": lng_val},
            },
        )

    await _assert_session_uploadable(db, student.id, submission_id)

    return await _handle_photo_upload_and_save(
        db=db,
        student_id=student.id,
        session_id=submission_id,
        meta_captured_at=cap,
        lat=float(lat_val),
        lng=float(lng_val),
        sha256=sha,
        image=upload,
        seq_no=int(start_seq),
    )