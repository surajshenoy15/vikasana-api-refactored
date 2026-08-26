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
from app.features.college_access.service import ensure_college_is_active

from app.features.faculty.role_policy import is_website_management_role
from app.features.faculty.role_policy import normalize_faculty_role

from app.features.faculty.permission_scope import WebsiteFacultyScope
from app.features.faculty.permission_scope import resolve_website_faculty_scope

from app.features.organization.service import require_department_architecture_enabled

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

        if payload.get("type") != "access":
            raise not_authenticated

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

    # ✅ SaaS college-wide access control
    await ensure_college_is_active(db, faculty.college)

    return faculty


async def get_current_website_faculty(
    current_faculty: Faculty = Depends(
        get_current_faculty
    ),
) -> Faculty:
    """
    Require an authenticated Faculty account with a website
    management role.

    This guard reuses get_current_faculty, so account activation and
    college SaaS checks remain unchanged. It performs no database
    write and does not modify the Faculty record.
    """
    faculty_role = getattr(
        current_faculty,
        "role",
        None,
    )

    if not is_website_management_role(faculty_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This faculty account does not have "
                "website management access"
            ),
        )

    # Validate the value without mutating the Faculty ORM object.
    # Role-specific guards can normalise locally when required.
    normalize_faculty_role(faculty_role)

    return current_faculty


async def get_current_website_faculty_scope(
    current_faculty: Faculty = Depends(
        get_current_website_faculty
    ),
) -> WebsiteFacultyScope:
    """
    Resolve an authenticated website Faculty account into an
    immutable authorization scope.

    The dependency performs no database write and does not mutate
    the Faculty ORM object.
    """
    try:
        return resolve_website_faculty_scope(
            current_faculty
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


async def get_current_enabled_website_faculty_scope(
    scope: WebsiteFacultyScope = Depends(
        get_current_website_faculty_scope
    ),
    db: AsyncSession = Depends(get_db),
) -> WebsiteFacultyScope:
    """
    Require an authenticated website Faculty scope whose college has
    department-wise architecture enabled.

    The organization-mode check is read-only. This dependency does
    not mutate the scope, Faculty account, or organization settings.
    """
    await require_department_architecture_enabled(
        db,
        scope.college,
    )

    return scope


def _get_student_lifecycle_status(
    student: Student,
) -> str:
    """
    Return normalized Student lifecycle status.

    Backward compatibility:
    rows/models without lifecycle_status are treated as ACTIVE.
    Handles plain strings and Enum-like values.
    """

    value = getattr(
        student,
        "lifecycle_status",
        "ACTIVE",
    )

    enum_value = getattr(
        value,
        "value",
        value,
    )

    normalized = str(
        enum_value
        or "ACTIVE"
    ).strip().upper()

    return (
        normalized
        or "ACTIVE"
    )


async def get_current_student(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Student:
    """
    Student auth guard dependency.

    ✅ Supports BOTH token styles:
    A) Current student OTP token:
       payload["sub"] = student_email
       payload["role"] = "student"

    B) Future improved token style:
       payload["sub"] = student_id (numeric string)
       payload["type"] = "access"
       payload["role"] = "student"

    We enforce role when present.
    We DO NOT require 'type' for student tokens.
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

    if sub.isdigit():
        result = await db.execute(select(Student).where(Student.id == int(sub)))
    else:
        result = await db.execute(select(Student).where(Student.email == sub))

    student = result.scalar_one_or_none()

    if student is None:
        raise not_authenticated

    if hasattr(student, "is_active") and not student.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This student account has been deactivated",
        )

    lifecycle_status = (
        _get_student_lifecycle_status(
            student
        )
    )

    if lifecycle_status in {
        "ARCHIVED",
        "PURGED",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This student record is no longer available "
                "for student portal access"
            ),
        )

    # ACTIVE and GRADUATED students may authenticate.
    #
    # GRADUATED students retain read access to their
    # profile, history and certificates.
    #
    # Operational writes use get_current_active_student.

    # ✅ SaaS college-wide access control
    await ensure_college_is_active(db, student.college)

    return student

async def get_current_active_student(
    student: Student = Depends(
        get_current_student
    ),
) -> Student:
    """
    Require an academically ACTIVE Student.

    Use this dependency for operations that create or mutate
    operational student activity data.

    ACTIVE:
        write access allowed.

    GRADUATED:
        authenticated read access remains available through
        get_current_student, but activity writes are blocked.

    ARCHIVED / PURGED:
        already rejected by get_current_student.
    """

    lifecycle_status = (
        _get_student_lifecycle_status(
            student
        )
    )

    if lifecycle_status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This student has completed the active academic "
                "lifecycle and cannot create or modify activities"
            ),
        )

    return student
