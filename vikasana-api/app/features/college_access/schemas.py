from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CollegeAccessToggleRequest(BaseModel):
    college: str = Field(..., min_length=1)
    reason: Optional[str] = None


class CollegeAccessStatusResponse(BaseModel):
    college: str
    is_active: bool
    reason: Optional[str] = None
    deactivated_at: Optional[datetime] = None