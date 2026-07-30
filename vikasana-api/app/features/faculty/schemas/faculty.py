from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.features.faculty.role_policy import normalize_faculty_role


class FacultyCreateRequest(BaseModel):
    full_name: str = Field(..., max_length=150)
    college: str = Field(..., max_length=200)
    email: EmailStr
    role: str = "faculty"
    department_id: int | None = Field(default=None, ge=1)


    @field_validator("role")
    @classmethod
    def normalize_role(
        cls,
        value: str,
    ) -> str:
        return normalize_faculty_role(value)


class FacultyUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    college: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    role: str | None = Field(default=None, min_length=1, max_length=50)
    department_id: int | None = Field(default=None, ge=1)


    @field_validator("role")
    @classmethod
    def normalize_optional_role(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return normalize_faculty_role(value)


class FacultyResponse(BaseModel):
    id: int
    full_name: str
    college: str
    email: EmailStr
    role: str
    is_active: bool
    image_url: str | None = None
    created_at: datetime
    department_id: int | None = None
    legacy_college_scope: bool = False

    class Config:
        from_attributes = True


# ✅ UPDATED RESPONSE
class FacultyCreateResponse(BaseModel):
    faculty: FacultyResponse
    activation_email_sent: bool
    message: str


class ActivateFacultyResponse(BaseModel):
    detail: str