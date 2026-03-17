from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.service import get_me, login, faculty_login
from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.auth.models import Admin

from app.features.auth.schemas.auth import LoginRequest, LoginResponse, FacultyLoginResponse, MeResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Admin Login",
)
async def admin_login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await login(payload, db)


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