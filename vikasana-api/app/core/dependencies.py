from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.jwt import decode_access_token
from app.features.auth.models import Admin
from app.features.faculty.models import Faculty
from app.features.students.models import Student

bearer = HTTPBearer(auto_error=False)


def _not_authenticated_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Admin:
    not_authenticated = _not_authenticated_exception()

    print("🔐 get_current_admin called")
    print("🔐 credentials present:", bool(credentials))

    if not credentials:
        print("❌ No credentials received")
        raise not_authenticated

    try:
        token = credentials.credentials
        print("🔐 raw token prefix:", token[:30] if token else None)

        payload = decode_access_token(token)
        print("🔐 decoded payload:", payload)

        admin_id = int(payload["sub"])

        if payload.get("type") != "access":
            print("❌ token type invalid:", payload.get("type"))
            raise not_authenticated

    except (JWTError, KeyError, ValueError) as e:
        print("❌ token decode failed:", repr(e))
        raise not_authenticated

    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    print("🔐 admin found:", bool(admin), "admin_id:", admin_id)

    if admin is None:
        print("❌ admin not found for id:", admin_id)
        raise not_authenticated

    if not admin.is_active:
        print("❌ admin inactive:", admin_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This admin account has been deactivated",
        )

    print("✅ admin authenticated:", admin_id)
    return admin


async def get_current_faculty(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Faculty:
    not_authenticated = _not_authenticated_exception()

    if not credentials:
        raise not_authenticated

    try:
        payload = decode_access_token(credentials.credentials)
        faculty_id = int(payload["sub"])

        # ✅ faculty tokens require this
        if payload.get("type") != "access":
            raise not_authenticated

        # ✅ optional role enforcement
        role = payload.get("role")
        if role and role != "faculty":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized as faculty",
            )

    except (JWTError, KeyError, ValueError):
        raise not_authenticated

    result = await db.execute(select(Faculty).where(Faculty.id == faculty_id))
    faculty = result.scalar_one_or_none()

    if faculty is None:
        raise not_authenticated

    if hasattr(faculty, "is_active") and not faculty.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This faculty account has been deactivated",
        )

    return faculty


async def get_current_student(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Student:
    """
    Student auth guard dependency.

    ✅ Supports BOTH token styles:
    A) Current student OTP token (your current system):
       payload["sub"] = student_email
       payload["role"] = "student"
       (no payload["type"])

    B) Future improved token style:
       payload["sub"] = student_id (numeric string)
       payload["type"] = "access"
       payload["role"] = "student"

    We enforce role when present.
    We DO NOT require 'type' for student tokens (to match your current token).
    """

    not_authenticated = _not_authenticated_exception()

    if not credentials:
        raise not_authenticated

    try:
        payload = decode_access_token(credentials.credentials)

        role = payload.get("role")
        if role and role != "student":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized as student",
            )

        sub = payload.get("sub")
        if not sub:
            raise not_authenticated

        sub = str(sub).strip()

    except (JWTError, KeyError, ValueError):
        raise not_authenticated

    # ✅ If sub is numeric -> treat as student_id
    if sub.isdigit():
        result = await db.execute(select(Student).where(Student.id == int(sub)))
    else:
        # ✅ Otherwise treat sub as email (your current token)
        result = await db.execute(select(Student).where(Student.email == sub))

    student = result.scalar_one_or_none()

    if student is None:
        raise not_authenticated

    if hasattr(student, "is_active") and not student.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This student account has been deactivated",
        )

    return student