from datetime import datetime

from pydantic import BaseModel


class SuperAdminMeOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {
        "from_attributes": True,
    }


class AdminCreateRequest(BaseModel):
    name: str
    email: str
    password: str


class AdminUpdateRequest(BaseModel):
    name: str
    email: str
    new_password: str | None = None


class SuperAdminPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class SuperAdminPasswordOtpRequest(BaseModel):
    current_password: str


class SuperAdminPasswordOtpVerifyRequest(BaseModel):
    mfa_token: str
    otp: str
    new_password: str


class SuperAdminPasswordOtpStartResponse(BaseModel):
    mfa_token: str
    message: str
    expires_in: int = 300


class PasswordChangeResponse(BaseModel):
    message: str
    revoked_count: int


class AdminAccountOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {
        "from_attributes": True,
    }


class AdminAccountListResponse(BaseModel):
    items: list[AdminAccountOut]
    total: int
    offset: int
    limit: int


class AdminSessionOut(BaseModel):
    session_id: str
    admin_id: int

    device_id: str | None
    device_name: str | None
    device_model: str | None
    device_type: str | None
    platform: str | None

    os_name: str | None
    os_version: str | None

    browser_name: str | None
    browser_version: str | None

    app_name: str | None
    app_version: str | None

    ip_address: str | None
    country: str | None
    region: str | None
    city: str | None

    user_agent: str | None

    status: str

    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime | None

    revoked_at: datetime | None
    revoked_by_admin_id: int | None
    revoke_reason: str | None

    # Will be populated by the session-list endpoint when
    # the viewed session is the currently authenticated one.
    is_current: bool = False

    model_config = {
        "from_attributes": True,
    }


class AdminSessionListResponse(BaseModel):
    items: list[AdminSessionOut]
    total: int
    offset: int
    limit: int


class AdminSessionsRevokedResponse(BaseModel):
    admin_id: int
    revoked_count: int
    token_version: int
    message: str
