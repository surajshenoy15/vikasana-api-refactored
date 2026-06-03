from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.service import (
    get_me,
    login,
    faculty_login,
    verify_admin_mfa,
)
from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.auth.models import Admin

from app.features.auth.schemas.auth import (
    LoginRequest,
    LoginResponse,
    FacultyLoginResponse,
    MeResponse,
    AdminMFAStartResponse,
    AdminMFAVerifyRequest,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=AdminMFAStartResponse,
    summary="Admin Login Step 1 - Email Password",
)
async def admin_login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminMFAStartResponse:
    return await login(payload, db)


@router.post(
    "/verify-mfa",
    response_model=LoginResponse,
    summary="Admin Login Step 2 - Verify MFA OTP",
)
async def admin_verify_mfa(
    payload: AdminMFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await verify_admin_mfa(payload, db)


@router.post(
    "/faculty/login",
    response_model=FacultyLoginResponse,
    summary="Faculty Login",
)
async def faculty_login_route(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> FacultyLoginResponse:
    return await faculty_login(payload, db)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get Current Admin",
)
async def me(
    current_admin: Admin = Depends(get_current_admin),
) -> MeResponse:
    return await get_me(current_admin)


@router.post(
    "/logout",
    summary="Logout",
)
async def logout() -> dict:
    return {"detail": "Logged out. Delete your token on the client side."}