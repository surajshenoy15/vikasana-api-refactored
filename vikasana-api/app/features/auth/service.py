from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, verify_password

from app.features.auth.models import Admin
from app.features.faculty.models import Faculty

from app.features.auth.schemas.auth import (
    AdminInfo,
    FacultyInfo,
    LoginRequest,
    LoginResponse,
    FacultyLoginResponse,
    MeResponse,
)


async def login(payload: LoginRequest, db: AsyncSession) -> LoginResponse:
    """
    Admin login
    """
    result = await db.execute(select(Admin).where(Admin.email == payload.email))
    admin = result.scalar_one_or_none()

    DUMMY_HASH = "$2b$12$dummyhashXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    password_ok = verify_password(payload.password, admin.password_hash if admin else DUMMY_HASH)

    if not admin or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact support.",
        )

    admin.last_login_at = datetime.now(timezone.utc)
    db.add(admin)
    await db.flush()

    token = create_access_token(admin.id, admin.email)

    return LoginResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        admin=AdminInfo(
            id=admin.id,
            name=admin.name,
            email=admin.email,
        ),
    )


async def faculty_login(payload: LoginRequest, db: AsyncSession) -> FacultyLoginResponse:
    """
    Faculty login (email + password)
    - Uses same timing-safe pattern as admin login
    - Requires faculty.is_active = True AND password_hash exists
    """
    result = await db.execute(select(Faculty).where(Faculty.email == payload.email))
    faculty = result.scalar_one_or_none()

    DUMMY_HASH = "$2b$12$dummyhashXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    password_ok = verify_password(payload.password, faculty.password_hash if (faculty and faculty.password_hash) else DUMMY_HASH)

    if not faculty or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not faculty.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not activated. Please activate your account.",
        )

    if not faculty.password_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password not set. Please activate your account.",
        )

    # If you want to track faculty last login, add column later.
    token = create_access_token(faculty.id, faculty.email)

    return FacultyLoginResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        faculty=FacultyInfo(
            id=faculty.id,
            full_name=faculty.full_name,
            email=faculty.email,
            college=faculty.college,
            role=faculty.role,
        ),
    )


async def get_me(admin: Admin) -> MeResponse:
    """Returns current admin profile. No DB call needed — admin already loaded by dependency."""
    return MeResponse(
        id=admin.id,
        name=admin.name,
        email=admin.email,
        is_active=admin.is_active,
        last_login_at=admin.last_login_at,
        created_at=admin.created_at,
    )