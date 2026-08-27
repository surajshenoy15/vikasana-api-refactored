from fastapi import APIRouter, Depends, Request
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
from app.features.audit.service import append_audit_log
from app.features.faculty.models import Faculty
from app.features.organization.models import Department

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
    request: Request,
    payload: AdminMFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    response = await verify_admin_mfa(
        payload,
        db,
        request=request,
    )

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=response.admin.id,
        actor_role=response.admin.role,
        actor_name=response.admin.name,
        actor_identifier=str(response.admin.id),
        actor_email=response.admin.email,
        action="LOGIN_SUCCESS",
        description="Admin logged in successfully after MFA verification.",
        entity_type="authentication",
        source="admin_web",
        request=request,
        metadata={
            "auth_method": "password_mfa_otp",
        },
    )

    return response


@router.post(
    "/faculty/login",
    response_model=FacultyLoginResponse,
    summary="Faculty Login",
)
async def faculty_login_route(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> FacultyLoginResponse:
    response = await faculty_login(payload, db)

    faculty_row = await db.get(
        Faculty,
        response.faculty.id,
    )

    department_name = None
    department_id = None

    if faculty_row is not None:
        department_id = faculty_row.department_id

        if department_id is not None:
            department = await db.get(
                Department,
                department_id,
            )

            if department is not None:
                department_name = department.name

    role = (
        str(response.faculty.role or "")
        .strip()
        .lower()
    )

    if role == "college_coordinator":
        actor_type = "COLLEGE_COORDINATOR"
    elif role == "hod":
        actor_type = "HOD"
    else:
        actor_type = "FACULTY"

    source = (
        "website_portal"
        if role in {
            "college_coordinator",
            "hod",
            "faculty_coordinator",
        }
        else "mobile_app"
    )

    await append_audit_log(
        db,
        actor_type=actor_type,
        actor_id=response.faculty.id,
        actor_role=role or None,
        actor_name=response.faculty.full_name,
        actor_identifier=str(response.faculty.id),
        actor_email=response.faculty.email,
        college=response.faculty.college,
        department_id=department_id,
        department_name=department_name,
        action="LOGIN_SUCCESS",
        description=(
            f"{response.faculty.full_name} logged in successfully "
            f"with role {role or 'faculty'}."
        ),
        entity_type="authentication",
        source=source,
        request=request,
        metadata={
            "auth_method": "password",
        },
    )

    return response


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