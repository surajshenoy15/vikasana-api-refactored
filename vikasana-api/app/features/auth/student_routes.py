from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError

from app.core.database import get_db
from app.core.jwt import decode_access_token, create_access_token
from app.features.auth.schemas.student_auth import (
    StudentRequestOtp,
    StudentVerifyOtp,
    StudentLoginResponse,
    StudentRefreshRequest,
)
from app.features.auth.student_auth_service import (
    request_student_otp,
    verify_student_otp_and_issue_token,
)


router = APIRouter(prefix="/auth/student", tags=["Auth - Student"])


@router.post("/request-otp")
async def request_otp(
    payload: StudentRequestOtp,
    db: AsyncSession = Depends(get_db)
):
    await request_student_otp(db, str(payload.email))
    return {"message": "OTP sent to email"}


@router.post("/verify-otp", response_model=StudentLoginResponse)
async def verify_otp(
    payload: StudentVerifyOtp,
    db: AsyncSession = Depends(get_db)
):
    tokens = await verify_student_otp_and_issue_token(
        db,
        str(payload.email),
        payload.otp
    )

    return StudentLoginResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens.get("token_type", "bearer")
    )


@router.post("/refresh", response_model=StudentLoginResponse)
async def refresh_student_token(payload: StudentRefreshRequest):
    try:
        decoded = decode_access_token(payload.refresh_token)

        if decoded.get("token_type") != "refresh":
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token"
            )

        email = decoded.get("sub")
        role = decoded.get("role")

        if not email or role != "student":
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token"
            )

        new_access_token = create_access_token({
            "sub": email,
            "role": "student"
        })

        return StudentLoginResponse(
            access_token=new_access_token,
            refresh_token=payload.refresh_token,
            token_type="bearer"
        )

    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Refresh token expired or invalid"
        )