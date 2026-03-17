from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.features.activities.models import ActivitySessionStatus


class AdminPhotoOut(BaseModel):
    id: int
    session_id: int
    student_id: int
    image_url: str
    captured_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AdminFaceCheckOut(BaseModel):
    id: int
    matched: bool
    cosine_score: Optional[float] = None
    l2_score: Optional[float] = None
    total_faces: Optional[int] = None
    raw_image_url: str
    processed_object: Optional[str] = None
    processed_url: Optional[str] = None   # ✅ ADD
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminSessionListItemOut(BaseModel):
    id: int
    student_id: int
    activity_type_id: int
    activity_name: str
    status: ActivitySessionStatus
    submitted_at: Optional[datetime] = None
    flag_reason: Optional[str] = None
    created_at: datetime

    # quick summary
    photos_count: int
    latest_face_matched: Optional[bool] = None
    latest_face_reason: Optional[str] = None
    latest_face_score: Optional[float] = None

    # ✅ NEW: Presigned URLs for UI thumbnails
    latest_face_processed_url: Optional[str] = None
    latest_face_raw_url: Optional[str] = None

    class Config:
        from_attributes = True


class AdminSessionDetailOut(BaseModel):
    id: int
    student_id: int
    activity_type_id: int
    activity_name: str
    description: Optional[str] = None
    status: ActivitySessionStatus
    started_at: datetime
    expires_at: datetime
    submitted_at: Optional[datetime] = None
    flag_reason: Optional[str] = None
    created_at: datetime

    photos: List[AdminPhotoOut]
    latest_face_check: Optional[AdminFaceCheckOut] = None

    # ✅ NEW: Presigned URLs for UI
    latest_face_processed_url: Optional[str] = None
    latest_face_raw_url: Optional[str] = None

    class Config:
        from_attributes = True


class RejectSessionIn(BaseModel):
    reason: str