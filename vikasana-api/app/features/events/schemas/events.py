# app/schemas/events.py  ✅ FULLY UPDATED (models aligned + event geofence + event submission photos gps)
from __future__ import annotations

from typing import Optional, List, Any
from datetime import datetime, date, time

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.config import ConfigDict


# =========================================================
# ------------------ EVENTS (CREATE / UPDATE / OUT) --------
# =========================================================

DEFAULT_EVENT_RADIUS_M = 500


class EventCreateIn(BaseModel):
    """
    Used for POST /admin/events
    Frontend may send activity_type_ids in multiple shapes/keys.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: str
    description: Optional[str] = None

    required_photos: int = Field(default=3, ge=3, le=5)

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    thumbnail_url: Optional[str] = None

    # ✅ Location fields
    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    # ✅ Event ↔ ActivityType mapping
    activity_type_ids: List[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_activity_keys(cls, data: Any):
        if not isinstance(data, dict):
            return data

        if "activity_type_ids" not in data or not data.get("activity_type_ids"):
            for k in ["activityTypeIds", "activityTypes", "activity_types", "activity_type_id", "activity_list"]:
                if k in data and data.get(k) is not None:
                    data["activity_type_ids"] = data.get(k)
                    break
        return data

    @field_validator("activity_type_ids", mode="before")
    @classmethod
    def _coerce_activity_type_ids(cls, v: Any):
        if v is None:
            return []

        # "6,7"
        if isinstance(v, str):
            parts = [x.strip() for x in v.split(",") if x.strip()]
            out: List[int] = []
            for x in parts:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out

        # single int
        if isinstance(v, int):
            return [v]

        # list of dicts: [{id: 6}, {id: 7}]
        if isinstance(v, list) and v and isinstance(v[0], dict):
            out: List[int] = []
            for obj in v:
                try:
                    out.append(int(obj.get("id")))
                except Exception:
                    pass
            return out

        # list of strings/ints
        if isinstance(v, list):
            out: List[int] = []
            for x in v:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out

        return []


class EventUpdateIn(BaseModel):
    """
    ✅ Used for PUT/PATCH /admin/events/{id}
    Partial updates supported (prevents 422).
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: Optional[str] = None
    description: Optional[str] = None

    required_photos: Optional[int] = Field(default=None, ge=3, le=5)

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    is_active: Optional[bool] = None
    thumbnail_url: Optional[str] = None

    # ✅ Location fields
    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    # ✅ mapping (optional on update)
    activity_type_ids: Optional[List[int]] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_activity_keys(cls, data: Any):
        if not isinstance(data, dict):
            return data

        if "activity_type_ids" not in data or data.get("activity_type_ids") is None:
            for k in ["activityTypeIds", "activityTypes", "activity_types", "activity_type_id", "activity_list"]:
                if k in data and data.get(k) is not None:
                    data["activity_type_ids"] = data.get(k)
                    break
        return data

    @field_validator("activity_type_ids", mode="before")
    @classmethod
    def _coerce_activity_type_ids(cls, v: Any):
        # On update, None means "do not change mapping"
        if v is None:
            return None

        if isinstance(v, str):
            parts = [x.strip() for x in v.split(",") if x.strip()]
            out: List[int] = []
            for x in parts:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out

        if isinstance(v, int):
            return [v]

        if isinstance(v, list) and v and isinstance(v[0], dict):
            out: List[int] = []
            for obj in v:
                try:
                    out.append(int(obj.get("id")))
                except Exception:
                    pass
            return out

        if isinstance(v, list):
            out: List[int] = []
            for x in v:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out

        return []


class EventOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    required_photos: int
    is_active: bool

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    thumbnail_url: Optional[str] = None

    # ✅ Location
    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ThumbnailUploadUrlIn(BaseModel):
    filename: str
    content_type: str


class ThumbnailUploadUrlOut(BaseModel):
    upload_url: str
    public_url: str


# =========================================================
# ------------------ REGISTRATION -------------------------
# =========================================================

class RegisterOut(BaseModel):
    submission_id: int
    status: str


# =========================================================
# ------------------ PHOTOS (EVENT SUBMISSION PHOTOS) ------
# =========================================================

class EventSubmissionPhotoOut(BaseModel):
    """
    ✅ Matches app/models/events.py -> EventSubmissionPhoto
    """
    id: int
    submission_id: int
    seq_no: int
    image_url: str

    lat: Optional[float] = None
    lng: Optional[float] = None
    distance_m: Optional[float] = None
    is_in_geofence: Optional[bool] = None

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PhotosUploadOut(BaseModel):
    """
    ✅ Used by POST /student/submissions/{submission_id}/photos
    """
    submission_id: int
    photos: List[EventSubmissionPhotoOut]


# =========================================================
# ------------------ SUBMISSION ---------------------------
# =========================================================

class FinalSubmitIn(BaseModel):
    description: str


class SubmissionOut(BaseModel):
    id: int
    event_id: int
    student_id: int
    status: str
    description: Optional[str] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    awarded_points: int = 0
    points_credited: bool = False

    model_config = ConfigDict(from_attributes=True)

class AdminSubmissionOut(BaseModel):
    id: int
    event_id: int
    student_id: int
    status: str
    description: Optional[str] = None

    created_at: datetime
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    awarded_points: int = 0
    points_credited: bool = False

    face_matched: Optional[bool] = None
    face_reason: Optional[str] = None
    cosine_score: Optional[float] = None
    flag_reason: Optional[str] = None
    photos: Optional[List[EventSubmissionPhotoOut]] = None

    model_config = ConfigDict(from_attributes=True)


class RejectIn(BaseModel):
    reason: str = Field(..., min_length=1)