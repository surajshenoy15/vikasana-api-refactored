import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_service import send_student_otp_email
from app.core.jwt import create_access_token, create_refresh_token
from app.features.auth.models import StudentOtpSession
from app.features.college_access.service import ensure_college_is_active
from app.features.students.models import Student


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Apple App Review configuration
# ---------------------------------------------------------

APP_REVIEW_ENABLED = (
    os.getenv("APP_REVIEW_ENABLED", "false").strip().lower() == "true"
)

APP_REVIEW_EMAIL = (
    os.getenv("APP_REVIEW_EMAIL", "").strip().lower()
)

APP_REVIEW_OTP = (
    os.getenv("APP_REVIEW_OTP", "").strip()
)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _is_valid_review_configuration() -> bool:
    return (
        APP_REVIEW_ENABLED
        and bool(APP_REVIEW_EMAIL)
        and len(APP_REVIEW_OTP) == 6
        and APP_REVIEW_OTP.isdigit()
    )


def _is_app_review_account(email: str) -> bool:
    """
    Returns True only when:
    1. App Review access is enabled.
    2. The configured OTP is a valid six-digit value.
    3. The entered email exactly matches the reviewer email.
    """
    if not _is_valid_review_configuration():
        return False

    normalized_email = _normalize_email(email)

    return hmac.compare_digest(
        normalized_email,
        APP_REVIEW_EMAIL,
    )


# ---------------------------------------------------------
# OTP helpers
# ---------------------------------------------------------

def _otp() -> str:
    """
    Generate a cryptographically secure six-digit OTP.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _eq(first_value: str, second_value: str) -> bool:
    return hmac.compare_digest(
        first_value,
        second_value,
    )


# ---------------------------------------------------------
# Request student OTP
# ---------------------------------------------------------
APPLE_REVIEW_EMAIL = "appletestreviewer@gmail.com"
APPLE_REVIEW_OTP = "123456"


async def request_student_otp(db: AsyncSession, email: str) -> None:
    email = email.strip().lower()

    q = await db.execute(
        select(Student).where(Student.email == email)
    )
    student = q.scalar_one_or_none()

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student not found with this email"
        )

    await ensure_college_is_active(db, student.college)

    is_apple_reviewer = email == APPLE_REVIEW_EMAIL

    # Fixed OTP only for Apple's reviewer account
    if is_apple_reviewer:
        otp = APPLE_REVIEW_OTP
    else:
        otp = _otp()

    sess = StudentOtpSession(
        email=email,
        otp_hash=_hash(otp),
        otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        attempts=0,
    )

    db.add(sess)
    await db.commit()

    # Apple already knows the fixed OTP, so don't send email
    if is_apple_reviewer:
        print("Apple App Review OTP session created")
        return

    # Normal students still receive OTP emails
    await send_student_otp_email(
        to_email=email,
        to_name=student.name,
        otp=otp
    )


# ---------------------------------------------------------
# Verify OTP and issue tokens
# ---------------------------------------------------------

async def verify_student_otp_and_issue_token(
    db: AsyncSession,
    email: str,
    otp: str,
) -> dict:
    normalized_email = _normalize_email(email)
    normalized_otp = otp.strip()

    if len(normalized_otp) != 6 or not normalized_otp.isdigit():
        raise HTTPException(
            status_code=400,
            detail="OTP must be a six-digit number",
        )

    # Find the newest unused OTP session.
    otp_query = await db.execute(
        select(StudentOtpSession)
        .where(
            func.lower(StudentOtpSession.email)
            == normalized_email
        )
        .where(StudentOtpSession.used_at.is_(None))
        .order_by(StudentOtpSession.id.desc())
        .limit(1)
    )

    otp_session = otp_query.scalar_one_or_none()

    if not otp_session:
        raise HTTPException(
            status_code=400,
            detail="OTP not requested or already used",
        )

    now = datetime.now(timezone.utc)

    if otp_session.otp_expires_at < now:
        raise HTTPException(
            status_code=400,
            detail="OTP expired. Please request again.",
        )

    if otp_session.attempts >= 5:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Request OTP again.",
        )

    otp_session.attempts += 1

    if not _eq(
        otp_session.otp_hash,
        _hash(normalized_otp),
    ):
        await db.commit()

        raise HTTPException(
            status_code=400,
            detail="Invalid OTP",
        )

    student_query = await db.execute(
        select(Student).where(
            func.lower(Student.email)
            == normalized_email
        )
    )

    student = student_query.scalar_one_or_none()

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student not found with this email",
        )

    await ensure_college_is_active(
        db,
        student.college,
    )

    otp_session.used_at = now
    await db.commit()

    access_token = create_access_token(
        {
            "sub": normalized_email,
            "role": "student",
        }
    )

    refresh_token = create_refresh_token(
        {
            "sub": normalized_email,
            "role": "student",
        }
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }