from pydantic import BaseModel, EmailStr, Field


class StudentRequestOtp(BaseModel):
    email: EmailStr


class StudentVerifyOtp(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=4, max_length=8)


class StudentLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"