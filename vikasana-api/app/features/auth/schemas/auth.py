from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Request Body ──────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "admin@vikasanafoundation.org",
                "password": "YourPassword123",
            }
        }
    }


# ── Response Bodies ───────────────────────────────────────────────────
class AdminInfo(BaseModel):
    """
    Safe admin info sent to the frontend after login.
    password_hash is never included here.
    """
    id: int
    name: str
    email: str
    role: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds — frontend uses this to know when token expires
    admin: AdminInfo

class AdminMFAStartResponse(BaseModel):
    """
    Response after admin email + password is correct.
    Access token is NOT returned yet.
    Frontend should show OTP screen.
    """
    mfa_required: bool = True
    mfa_token: str
    message: str = "OTP sent to admin email"
    expires_in: int = 300


class AdminMFAVerifyRequest(BaseModel):
    """
    Request body for verifying admin OTP.

    Device fields are optional for backward compatibility.
    Existing Admin frontend clients can continue sending only
    mfa_token + otp.
    """

    mfa_token: str
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
    )

    device_id: str | None = Field(
        default=None,
        max_length=255,
    )

    device_name: str | None = Field(
        default=None,
        max_length=255,
    )

    device_model: str | None = Field(
        default=None,
        max_length=255,
    )

    device_type: str | None = Field(
        default=None,
        max_length=50,
    )

    platform: str | None = Field(
        default=None,
        max_length=50,
    )

    os_name: str | None = Field(
        default=None,
        max_length=100,
    )

    os_version: str | None = Field(
        default=None,
        max_length=100,
    )

    browser_name: str | None = Field(
        default=None,
        max_length=100,
    )

    browser_version: str | None = Field(
        default=None,
        max_length=100,
    )

    app_name: str | None = Field(
        default=None,
        max_length=100,
    )

    app_version: str | None = Field(
        default=None,
        max_length=100,
    )

class MeResponse(BaseModel):
    """Full admin profile — returned by GET /auth/me"""
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Faculty Login Response ────────────────────────────────────────────
class FacultyInfo(BaseModel):
    """
    Safe faculty info sent to the frontend after faculty login.
    """
    id: int
    full_name: str
    email: EmailStr
    college: str
    role: str

    model_config = {"from_attributes": True}


class FacultyLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    faculty: FacultyInfo