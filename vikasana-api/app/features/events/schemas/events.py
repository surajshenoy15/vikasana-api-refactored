from __future__ import annotations

from typing import Optional, List, Any
from datetime import datetime, date, time

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.config import ConfigDict


DEFAULT_EVENT_RADIUS_M = 500


# =========================================================
# ------------------ SCORING RULES ------------------------
# =========================================================

class EventScoringRuleIn(BaseModel):
    activity_type_id: int
    score_mode: str = "AUTO"
    manual_points: Optional[int] = None
    min_required_hours: Optional[float] = None

    @field_validator("score_mode", mode="before")
    @classmethod
    def _normalize_score_mode(cls, v: Any):
        if v is None:
            return "AUTO"
        s = str(v).strip().upper()
        return s if s in {"AUTO", "MANUAL"} else "AUTO"


class EventScoringRuleOut(BaseModel):
    activity_type_id: int
    score_mode: str = "AUTO"
    manual_points: Optional[int] = None
    min_required_hours: Optional[float] = None


# =========================================================
# ------------------ EVENTS (CREATE / UPDATE / OUT) -------
# =========================================================

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

    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    activity_type_ids: List[int] = Field(default_factory=list)
    custom_activities: List[str] = Field(default_factory=list)
    scoring_rules: List[EventScoringRuleIn] = Field(default_factory=list)

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

    @field_validator("custom_activities", mode="before")
    @classmethod
    def _coerce_custom_activities(cls, v: Any):
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


class EventUpdateIn(BaseModel):
    """
    Used for PUT/PATCH /admin/events/{id}
    Partial updates supported.
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

    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    activity_type_ids: Optional[List[int]] = None
    custom_activities: Optional[List[str]] = None
    scoring_rules: Optional[List[EventScoringRuleIn]] = None

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

    @field_validator("custom_activities", mode="before")
    @classmethod
    def _coerce_custom_activities(cls, v: Any):
        if v is None:
            return None
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
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

    venue_name: Optional[str] = None
    maps_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None

    activity_type_ids: List[int] = Field(default_factory=list)
    scoring_rules: List[EventScoringRuleOut] = Field(default_factory=list)

    # live count
    registered_count: int = 0
    capacity: Optional[int] = None
    max_participants: Optional[int] = None

    # ✅ NEW: points display (computed in /student/events)
    points_mode: Optional[str] = None           # fixed | auto | mixed | none
    manual_points_total: Optional[int] = None
    auto_max_total: Optional[int] = None
    max_points: Optional[int] = None
    min_required_hours: Optional[float] = None
    points_display: Optional[str] = None
    points: Optional[int] = None

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
# ------------------ PHOTOS -------------------------------
# =========================================================

class EventSubmissionPhotoOut(BaseModel):
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