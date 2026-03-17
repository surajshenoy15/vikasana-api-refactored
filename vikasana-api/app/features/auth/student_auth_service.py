import os
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.features.students.models import Student
from app.features.auth.models import StudentOtpSession
from app.core.email_service import send_student_otp_email


def _otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def _hash(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


async def request_student_otp(db: AsyncSession, email: str) -> None:
    q = await db.execute(select(Student).where(Student.email == email))
    student = q.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found with this email")

    otp = _otp()
    print("\n========= STUDENT OTP =========")
    print(f"EMAIL: {email}")
    print(f"OTP  : {otp}")
    print("================================\n")

    sess = StudentOtpSession(
        email=email,
        otp_hash=_hash(otp),
        otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        attempts=0,
    )
    db.add(sess)
    await db.commit()

    await send_student_otp_email(to_email=email, to_name=student.name, otp=otp)


async def verify_student_otp_and_issue_token(db: AsyncSession, email: str, otp: str) -> str:
    # latest non-used session
    q = await db.execute(
        select(StudentOtpSession)
        .where(StudentOtpSession.email == email)
        .where(StudentOtpSession.used_at.is_(None))
        .order_by(StudentOtpSession.id.desc())
        .limit(1)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=400, detail="OTP not requested or already used")

    if sess.otp_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired. Please request again.")

    if sess.attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Request OTP again.")

    sess.attempts += 1

    if not _eq(sess.otp_hash, _hash(otp)):
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid OTP")

    sess.used_at = datetime.now(timezone.utc)
    await db.commit()

    # ✅ issue JWT token (hook into your existing JWT util)
    # Replace below line with your existing token creator
    from app.core.jwt import create_access_token  # <-- IF you have it
    token = create_access_token({"sub": email, "role": "student"})

    return token