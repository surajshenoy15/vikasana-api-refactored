from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.jwt import (
    create_access_token,
    decode_access_token,
)
from app.features.auth.schemas.student_auth import (
    StudentLoginResponse,
    StudentRefreshRequest,
    StudentRequestOtp,
    StudentVerifyOtp,
)
from app.features.audit.service import append_audit_log
from app.features.students.models import Student
from app.features.organization.models import Department

from app.features.auth.student_auth_service import (
    request_student_otp,
    verify_student_otp_and_issue_token,
)


router = APIRouter(
    prefix="/auth/student",
    tags=["Auth - Student"],
)


@router.post("/request-otp")
async def request_otp(
    payload: StudentRequestOtp,
    db: AsyncSession = Depends(get_db),
):
    await request_student_otp(
        db,
        str(payload.email),
    )

    return {
        "message": "OTP sent successfully"
    }


@router.post(
    "/verify-otp",
    response_model=StudentLoginResponse,
)
async def verify_otp(
    request: Request,
    payload: StudentVerifyOtp,
    db: AsyncSession = Depends(get_db),
):
    tokens = await verify_student_otp_and_issue_token(
        db=db,
        email=str(payload.email),
        otp=payload.otp,
    )

    normalized_email = (
        str(payload.email)
        .strip()
        .lower()
    )

    student_result = await db.execute(
        select(Student).where(
            func.lower(Student.email)
            == normalized_email
        )
    )

    student = student_result.scalar_one_or_none()

    if student is not None:
        department_name = None

        if student.department_id is not None:
            department = await db.get(
                Department,
                student.department_id,
            )

            if department is not None:
                department_name = department.name

        await append_audit_log(
            db,
            actor_type="STUDENT",
            actor_id=student.id,
            actor_role="student",
            actor_name=student.name,
            actor_identifier=student.usn,
            actor_email=student.email,
            college=student.college,
            department_id=student.department_id,
            department_name=department_name,
            action="LOGIN_SUCCESS",
            description=(
                f"{student.name} logged in successfully."
            ),
            entity_type="authentication",
            source="mobile_app",
            request=request,
            metadata={
                "auth_method": "otp",
            },
        )

    return StudentLoginResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens.get(
            "token_type",
            "bearer",
        ),
    )


@router.post(
    "/refresh",
    response_model=StudentLoginResponse,
)
async def refresh_student_token(
    payload: StudentRefreshRequest,
):
    try:
        decoded = decode_access_token(
            payload.refresh_token
        )

        if decoded.get("token_type") != "refresh":
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token",
            )

        email = decoded.get("sub")
        role = decoded.get("role")

        if not email or role != "student":
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token",
            )

        new_access_token = create_access_token(
            {
                "sub": email,
                "role": "student",
            }
        )

        return StudentLoginResponse(
            access_token=new_access_token,
            refresh_token=payload.refresh_token,
            token_type="bearer",
        )

    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Refresh token expired or invalid",
        )