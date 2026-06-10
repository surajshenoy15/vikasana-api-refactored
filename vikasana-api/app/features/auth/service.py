from datetime import datetime, timezone, timedelta
import hashlib
import secrets

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, verify_password

from app.features.auth.models import Admin, AdminMFAOtp
from app.features.faculty.models import Faculty
from app.features.college_access.service import ensure_college_is_active
from app.core.email_service import send_admin_mfa_otp_email

from app.features.auth.schemas.auth import (
    AdminInfo,
    FacultyInfo,
    LoginRequest,
    LoginResponse,
    FacultyLoginResponse,
    MeResponse,
    AdminMFAStartResponse,
    AdminMFAVerifyRequest,
)


# ─────────────────────────────────────────────────────────────
# Admin MFA helpers
# ─────────────────────────────────────────────────────────────

def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


async def _send_admin_mfa_email(email: str, name: str, otp: str):
    await send_admin_mfa_otp_email(
        to_email=email,
        to_name=name or "Admin",
        otp=otp,
    )


# ─────────────────────────────────────────────────────────────
# Admin login step 1: email + password
# ─────────────────────────────────────────────────────────────

async def login(payload: LoginRequest, db: AsyncSession) -> AdminMFAStartResponse:
    """
    Admin login step 1:
    - Verify admin email + password
    - Generate 6-digit OTP
    - Store OTP hash in DB
    - Return mfa_token
    - Do NOT return access token yet
    """
    result = await db.execute(select(Admin).where(Admin.email == payload.email))
    admin = result.scalar_one_or_none()

    DUMMY_HASH = "$2b$12$dummyhashXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    password_ok = verify_password(
        payload.password,
        admin.password_hash if admin else DUMMY_HASH,
    )

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

    # Invalidate previous unused OTP sessions for this admin
    await db.execute(
        update(AdminMFAOtp)
        .where(
            AdminMFAOtp.admin_id == admin.id,
            AdminMFAOtp.used == False,
        )
        .values(used=True)
    )

    otp = f"{secrets.randbelow(1000000):06d}"
    mfa_token = secrets.token_urlsafe(32)

    otp_row = AdminMFAOtp(
        admin_id=admin.id,
        otp_hash=_hash_otp(otp),
        mfa_token=mfa_token,
        attempts=0,
        used=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )

    db.add(otp_row)
    await db.commit()

    await _send_admin_mfa_email(admin.email, admin.name, otp)

    return AdminMFAStartResponse(
        mfa_required=True,
        mfa_token=mfa_token,
        message="OTP sent to admin email",
        expires_in=900,
    )


# ─────────────────────────────────────────────────────────────
# Admin login step 2: verify OTP
# ─────────────────────────────────────────────────────────────

async def verify_admin_mfa(
    payload: AdminMFAVerifyRequest,
    db: AsyncSession,
) -> LoginResponse:
    """
    Admin login step 2:
    - Verify mfa_token + OTP
    - Mark OTP as used
    - Update last_login_at
    - Return real access token
    """
    result = await db.execute(
        select(AdminMFAOtp)
        .where(AdminMFAOtp.mfa_token == payload.mfa_token)
        .order_by(AdminMFAOtp.id.desc())
    )
    otp_row = result.scalar_one_or_none()

    if not otp_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP session",
        )

    if otp_row.used:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP already used. Please login again.",
        )

    now = datetime.now(timezone.utc)

    if otp_row.expires_at < now:
        otp_row.used = True
        db.add(otp_row)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired. Please login again.",
        )

    if otp_row.attempts >= 5:
        otp_row.used = True
        db.add(otp_row)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP attempts. Please login again.",
        )

    submitted_otp = str(payload.otp).strip()

    if _hash_otp(submitted_otp) != otp_row.otp_hash:
        otp_row.attempts += 1
        db.add(otp_row)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OTP",
        )

    result = await db.execute(select(Admin).where(Admin.id == otp_row.admin_id))
    admin = result.scalar_one_or_none()

    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account not available",
        )

    otp_row.used = True
    admin.last_login_at = now

    db.add(otp_row)
    db.add(admin)
    await db.commit()

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


# ─────────────────────────────────────────────────────────────
# Faculty login unchanged
# ─────────────────────────────────────────────────────────────

async def faculty_login(payload: LoginRequest, db: AsyncSession) -> FacultyLoginResponse:
    """
    Faculty login email + password.

    MFA is only added for admin login.
    Faculty login remains unchanged.
    """
    result = await db.execute(select(Faculty).where(Faculty.email == payload.email))
    faculty = result.scalar_one_or_none()

    DUMMY_HASH = "$2b$12$dummyhashXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    password_ok = verify_password(
        payload.password,
        faculty.password_hash if (faculty and faculty.password_hash) else DUMMY_HASH,
    )

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

    # SaaS college-wide access control
    await ensure_college_is_active(db, faculty.college)

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


# ─────────────────────────────────────────────────────────────
# Current admin profile
# ─────────────────────────────────────────────────────────────

async def get_me(admin: Admin) -> MeResponse:
    """
    Returns current admin profile.
    No DB call needed because admin is already loaded by dependency.
    """
    return MeResponse(
        id=admin.id,
        name=admin.name,
        email=admin.email,
        is_active=admin.is_active,
        last_login_at=admin.last_login_at,
        created_at=admin.created_at,
    )