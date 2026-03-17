from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.features.auth.schemas.student_auth import StudentRequestOtp, StudentVerifyOtp, StudentLoginResponse
from app.features.auth.student_auth_service import request_student_otp, verify_student_otp_and_issue_token


router = APIRouter(prefix="/auth/student", tags=["Auth - Student"])


@router.post("/request-otp")
async def request_otp(payload: StudentRequestOtp, db: AsyncSession = Depends(get_db)):
    await request_student_otp(db, str(payload.email))
    return {"message": "OTP sent to email"}


@router.post("/verify-otp", response_model=StudentLoginResponse)
async def verify_otp(payload: StudentVerifyOtp, db: AsyncSession = Depends(get_db)):
    token = await verify_student_otp_and_issue_token(db, str(payload.email), payload.otp)
    return StudentLoginResponse(access_token=token)