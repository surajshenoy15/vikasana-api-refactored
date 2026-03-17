import os
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.features.faculty.models import Faculty
from app.features.faculty.models import FacultyActivationSession
from app.features.faculty.schemas.faculty import FacultyCreateRequest
from app.core.file_storage import upload_faculty_image
from app.core.email_service import send_activation_email, send_faculty_otp_email
from app.core.faculty_tokens import (
    create_activation_token,
    hash_token,
    verify_token,
    activation_expiry_dt,
)

# ✅ Use ONLY passlib via security.py — never import raw bcrypt directly.
# This ensures hash_password() and verify_password() always use the same
# bcrypt implementation and format, preventing the passlib checksum error.
from app.core.security import hash_password


# ---------------------------
# Helpers
# ---------------------------

def mask_email(email: str) -> str:
    """
    a*****z@domain.com
    """
    try:
        name, domain = email.split("@", 1)
    except ValueError:
        return email
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + ("*" * (len(name) - 2)) + name[-1]
    return masked + "@" + domain


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def generate_otp() -> str:
    # 6-digit OTP
    import random
    return f"{random.randint(0, 999999):06d}"


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ---------------------------
# Existing: Create Faculty + Send Activation Email (unchanged behavior)
# ---------------------------

async def create_faculty(
    payload: FacultyCreateRequest,
    db: AsyncSession,
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
    image_filename: str | None = None,
) -> tuple[Faculty, bool]:
    # check existing
    q = await db.execute(select(Faculty).where(Faculty.email == payload.email))
    existing = q.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Faculty already exists with this email")

    image_url = None
    if image_bytes and image_filename:
        image_url = await upload_faculty_image(
            file_bytes=image_bytes,
            content_type=image_content_type or "image/jpeg",
            filename=image_filename,
        )

    token = create_activation_token(payload.email)
    token_hash = hash_token(token)
    print("\n================ ACTIVATION TOKEN ================")
    print(f"EMAIL: {payload.email}")
    print(f"TOKEN: {token}")
    print(f"URL : http://31.97.230.171:8000/api/faculty/activation/validate?token={token}")
    print("==================================================\n")

    faculty = Faculty(
        full_name=payload.full_name,
        college=payload.college,
        email=payload.email,
        role=payload.role,
        is_active=False,
        activation_token_hash=token_hash,
        activation_expires_at=activation_expiry_dt(),
        image_url=image_url,
        # password_hash / password_set_at remain None initially
    )

    db.add(faculty)
    await db.commit()
    await db.refresh(faculty)

    # activation URL (frontend page) OR API activation endpoint
    frontend_base = os.getenv("FRONTEND_BASE_URL", "").rstrip("/")

    if frontend_base:
        activate_url = f"{frontend_base}/activate?token={token}"
    else:
        activate_url = "http://31.97.230.171:8000/api/faculty/activate?token=" + token

    # do not crash if email not configured
    email_sent = False
    try:
        await send_activation_email(
            to_email=faculty.email,
            to_name=faculty.full_name,
            activate_url=activate_url,
        )
        email_sent = True
    except Exception as e:
        print(f"[WARN] Activation email not sent for {faculty.email}: {e}")

    return faculty, email_sent


# ==========================================================
# NEW FLOW:
# 1) validate token -> create activation session
# 2) send OTP
# 3) verify OTP -> return set_password_token
# 4) set password -> activate account
# ==========================================================

async def validate_activation_token_and_create_session(
    token: str,
    db: AsyncSession,
) -> tuple[str, str, datetime]:
    """
    Validates activation link token. Creates activation session.
    Returns: (activation_session_id, masked_email, activation_link_expires_at)
    """
    max_age_seconds = int(os.getenv("ACTIVATION_TOKEN_EXPIRE_HOURS", "48")) * 3600

    try:
        data = verify_token(token, max_age_seconds=max_age_seconds)
        email = data.get("email")
        if not email:
            raise ValueError("Invalid token payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token")

    q = await db.execute(select(Faculty).where(Faculty.email == email))
    faculty = q.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    # Already fully activated (active + password set) => stop
    if faculty.is_active and getattr(faculty, "password_hash", None):
        raise HTTPException(status_code=400, detail="Account already activated")

    # Verify token hash stored in DB
    if not faculty.activation_token_hash or faculty.activation_token_hash != hash_token(token):
        raise HTTPException(status_code=400, detail="Invalid activation token")

    if faculty.activation_expires_at and faculty.activation_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Activation link expired")

    # Create activation session
    session_id = generate_session_id()
    sess = FacultyActivationSession(
        id=session_id,
        faculty_id=faculty.id,
        otp_hash=None,
        otp_expires_at=None,
        otp_attempts=0,
        otp_verified_at=None,
    )
    db.add(sess)
    await db.commit()

    return session_id, mask_email(faculty.email), faculty.activation_expires_at


async def send_activation_otp(
    activation_session_id: str,
    db: AsyncSession,
) -> None:
    """
    Generates OTP, stores hash+expiry in activation session and sends OTP email.
    """
    q = await db.execute(
        select(FacultyActivationSession).where(FacultyActivationSession.id == activation_session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    fq = await db.execute(select(Faculty).where(Faculty.id == sess.faculty_id))
    faculty = fq.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    otp = generate_otp()

    print("\n========= FACULTY OTP =========")
    print(f"EMAIL: {faculty.email}")
    print(f"OTP  : {otp}")
    print("================================\n")

    sess.otp_hash = hash_otp(otp)
    sess.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    await db.commit()

    await send_faculty_otp_email(
        to_email=faculty.email,
        to_name=faculty.full_name,
        otp=otp,
    )


async def verify_activation_otp(
    activation_session_id: str,
    otp: str,
    db: AsyncSession,
) -> str:
    """
    Verifies OTP. On success returns a short-lived set_password_token.
    """
    q = await db.execute(
        select(FacultyActivationSession).where(FacultyActivationSession.id == activation_session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    if not sess.otp_hash or not sess.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP not generated yet. Please send OTP first.")

    if sess.otp_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired. Please resend OTP.")

    if sess.otp_attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Please resend OTP.")

    ok = constant_time_equals(sess.otp_hash, hash_otp(otp))
    sess.otp_attempts += 1

    if not ok:
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid OTP")

    sess.otp_verified_at = datetime.now(timezone.utc)
    await db.commit()

    s_secret = os.getenv("ACTIVATION_TOKEN_SECRET", "change-me")
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret_key=s_secret, salt="faculty-activation")

    set_password_token = s.dumps({"session_id": activation_session_id, "purpose": "set-password"})
    return set_password_token


async def set_password_after_otp(
    set_password_token: str,
    new_password: str,
    db: AsyncSession,
) -> None:
    """
    Sets password, activates account, clears activation token, deletes session.

    ✅ Uses hash_password() from security.py (passlib/bcrypt) — the same
       function used everywhere else — so verify_password() will always work.
    """
    # Validate token (15 min)
    s_secret = os.getenv("ACTIVATION_TOKEN_SECRET", "change-me")
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret_key=s_secret, salt="faculty-activation")

    try:
        data = s.loads(set_password_token, max_age=15 * 60)
        if data.get("purpose") != "set-password":
            raise ValueError("Wrong token purpose")
        session_id = data.get("session_id")
        if not session_id:
            raise ValueError("Missing session_id")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired set password token")

    q = await db.execute(select(FacultyActivationSession).where(FacultyActivationSession.id == session_id))
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    if not sess.otp_verified_at:
        raise HTTPException(status_code=400, detail="OTP not verified")

    fq = await db.execute(select(Faculty).where(Faculty.id == sess.faculty_id))
    faculty = fq.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    # ✅ hash_password() imported from security.py — uses passlib, not raw bcrypt
    faculty.password_hash     = hash_password(new_password)
    faculty.password_set_at   = datetime.now(timezone.utc)
    faculty.is_active         = True

    # Clear activation token fields (single-use link)
    faculty.activation_token_hash = None
    faculty.activation_expires_at = None

    # Cleanup session
    await db.delete(sess)
    await db.commit()


# ----------------------------------------------------------
# OPTIONAL: Keep old activate_faculty() for backward compatibility
# ----------------------------------------------------------

async def activate_faculty(token: str, db: AsyncSession) -> None:
    """
    OLD behavior: activates immediately without OTP.
    Keep only if you want backward compatibility; otherwise remove route.
    """
    max_age_seconds = int(os.getenv("ACTIVATION_TOKEN_EXPIRE_HOURS", "48")) * 3600
    try:
        data = verify_token(token, max_age_seconds=max_age_seconds)
        email = data.get("email")
        if not email:
            raise ValueError("Invalid token payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token")

    q = await db.execute(select(Faculty).where(Faculty.email == email))
    faculty = q.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    if faculty.is_active:
        return

    if not faculty.activation_token_hash or faculty.activation_token_hash != hash_token(token):
        raise HTTPException(status_code=400, detail="Invalid activation token")

    if faculty.activation_expires_at and faculty.activation_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Activation link expired")

    faculty.is_active = True
    faculty.activation_token_hash = None
    faculty.activation_expires_at = None

    await db.commit()