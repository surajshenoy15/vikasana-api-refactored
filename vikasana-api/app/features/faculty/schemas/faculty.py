from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class FacultyCreateRequest(BaseModel):
    full_name: str = Field(..., max_length=150)
    college: str = Field(..., max_length=200)
    email: EmailStr
    role: str = "faculty"


class FacultyResponse(BaseModel):
    id: int
    full_name: str
    college: str
    email: EmailStr
    role: str
    is_active: bool
    image_url: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


# ✅ UPDATED RESPONSE
class FacultyCreateResponse(BaseModel):
    faculty: FacultyResponse
    activation_email_sent: bool
    message: str


class ActivateFacultyResponse(BaseModel):
    detail: str