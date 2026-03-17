from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ActivityTypeStatus(str, Enum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class SessionStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    FLAGGED = "FLAGGED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActivityTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    status: ActivityTypeStatus
    hours_per_unit: int
    points_per_unit: int
    max_points: int

    # ✅ optional (admin geo config) - include if you return these in API
    maps_url: Optional[str] = None
    target_lat: Optional[float] = None
    target_lng: Optional[float] = None
    radius_m: Optional[int] = None

    class Config:
        from_attributes = True


class RequestActivityTypeIn(BaseModel):
    name: str = Field(..., max_length=120)
    description: Optional[str] = Field(None, max_length=500)


class CreateSessionIn(BaseModel):
    activity_type_id: int
    activity_name: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=800)


class SessionOut(BaseModel):
    id: int
    activity_type_id: int
    activity_name: str
    description: Optional[str]
    session_code: str
    started_at: datetime
    expires_at: datetime
    status: SessionStatus
    duration_hours: Optional[float] = None
    flag_reason: Optional[str] = None

    class Config:
        from_attributes = True


class PhotoMetaIn(BaseModel):
    captured_at: datetime
    lat: float
    lng: float
    sha256: Optional[str] = None


class PhotoOut(BaseModel):
    id: int
    image_url: str
    sha256: str | None = None
    captured_at: datetime
    lat: float
    lng: float

    # IMPORTANT: default so Pydantic won't require ORM attribute
    is_duplicate: bool = False

    # ✅ NEW: geo fence fields (returned by API)
    distance_m: Optional[float] = None
    is_in_geofence: bool = True
    geo_flag_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SubmitSessionOut(BaseModel):
    session: SessionOut
    newly_awarded_points: int
    total_points_for_type: int
    total_hours_for_type: float


class SessionListItemOut(BaseModel):
    id: int
    activity_type_id: int
    activity_name: str
    description: Optional[str]
    started_at: datetime
    expires_at: datetime
    submitted_at: Optional[datetime]
    status: str
    duration_hours: Optional[float]
    flag_reason: Optional[str]


class TargetLocationOut(BaseModel):
    maps_url: Optional[str] = None
    target_lat: Optional[float] = None
    target_lng: Optional[float] = None
    radius_m: int = 500


class SessionDetailOut(SessionListItemOut):
    # ✅ NEW: show activity's target location to student/admin
    target_location: Optional[TargetLocationOut] = None

    photos: List["PhotoOut"] = []


# (Optional) If you use forward refs in some environments:
# SessionDetailOut.model_rebuild()