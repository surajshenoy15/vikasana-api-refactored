from datetime import datetime
from pydantic import BaseModel, Field

class ActivationValidateResponse(BaseModel):
    activation_session_id: str
    email_masked: str
    expires_at: datetime

class SendOtpRequest(BaseModel):
    activation_session_id: str

class VerifyOtpRequest(BaseModel):
    activation_session_id: str
    otp: str = Field(..., min_length=6, max_length=6)

class VerifyOtpResponse(BaseModel):
    set_password_token: str

class SetPasswordRequest(BaseModel):
    set_password_token: str
    new_password: str = Field(..., min_length=8, max_length=128)

class SetPasswordResponse(BaseModel):
    detail: str