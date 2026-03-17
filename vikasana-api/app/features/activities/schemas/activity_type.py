# app/schemas/activity_type.py

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.features.activities.models import ActivityTypeStatus


class ActivityTypeBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)

    status: ActivityTypeStatus = ActivityTypeStatus.APPROVED

    hours_per_unit: float = Field(default=20.0, gt=0)
    points_per_unit: int = Field(default=5, ge=0)
    max_points: int = Field(default=20, ge=0)

    maps_url: Optional[str] = None
    target_lat: Optional[float] = None
    target_lng: Optional[float] = None
    radius_m: int = Field(default=500, ge=1)

    is_active: bool = True


class ActivityTypeCreate(ActivityTypeBase):
    pass


class ActivityTypeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)

    status: Optional[ActivityTypeStatus] = None

    hours_per_unit: Optional[float] = Field(default=None, gt=0)
    points_per_unit: Optional[int] = Field(default=None, ge=0)
    max_points: Optional[int] = Field(default=None, ge=0)

    maps_url: Optional[str] = None
    target_lat: Optional[float] = None
    target_lng: Optional[float] = None
    radius_m: Optional[int] = Field(default=None, ge=1)

    is_active: Optional[bool] = None


class ActivityTypeOut(ActivityTypeBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True