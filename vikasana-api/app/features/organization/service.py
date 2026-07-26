from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.organization.models import (
    CollegeOrganizationSetting,
    Department,
)
from app.features.organization.schemas import (
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
)


# --------------------------------------------------
# SHARED HELPERS
# --------------------------------------------------


def normalize_college(college: str | None) -> str:
    normalized = (college or "").strip()

    if not normalized:
        raise HTTPException(
            status_code=400,
            detail="college is required",
        )

    return normalized


def _college_matches(column, college: str):
    return func.lower(func.trim(column)) == college.lower()


async def _commit_or_raise_conflict(
    db: AsyncSession,
    conflict_message: str,
) -> None:
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()

        raise HTTPException(
            status_code=409,
            detail=conflict_message,
        ) from error


# --------------------------------------------------
# ORGANISATION SETTINGS
# --------------------------------------------------


async def _find_organization_settings(
    db: AsyncSession,
    college: str,
) -> CollegeOrganizationSetting | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(CollegeOrganizationSetting).where(
            _college_matches(
                CollegeOrganizationSetting.college,
                normalized_college,
            )
        )
    )

    return result.scalar_one_or_none()


async def get_organization_settings(
    db: AsyncSession,
    college: str,
) -> OrganizationSettingsResponse:
    normalized_college = normalize_college(college)

    settings = await _find_organization_settings(
        db,
        normalized_college,
    )

    if settings is None:
        return OrganizationSettingsResponse(
            id=None,
            college=normalized_college,
            department_architecture_enabled=False,
            academic_year_start_month=7,
            created_at=None,
            updated_at=None,
        )

    return OrganizationSettingsResponse.model_validate(settings)


async def update_organization_settings(
    db: AsyncSession,
    payload: OrganizationSettingsUpdateRequest,
) -> OrganizationSettingsResponse:
    normalized_college = normalize_college(payload.college)

    settings = await _find_organization_settings(
        db,
        normalized_college,
    )

    if settings is None:
        settings = CollegeOrganizationSetting(
            college=normalized_college,
            department_architecture_enabled=(
                payload.department_architecture_enabled
            ),
            academic_year_start_month=(
                payload.academic_year_start_month
            ),
        )

        db.add(settings)
    else:
        settings.college = normalized_college
        settings.department_architecture_enabled = (
            payload.department_architecture_enabled
        )
        settings.academic_year_start_month = (
            payload.academic_year_start_month
        )

    await _commit_or_raise_conflict(
        db,
        "Organization settings already exist for this college",
    )

    await db.refresh(settings)

    return OrganizationSettingsResponse.model_validate(settings)


# --------------------------------------------------
# DEPARTMENT HELPERS
# --------------------------------------------------


async def _find_department(
    db: AsyncSession,
    department_id: int,
    college: str,
) -> Department | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            _college_matches(
                Department.college,
                normalized_college,
            ),
        )
    )

    return result.scalar_one_or_none()


# --------------------------------------------------
# DEPARTMENT READ OPERATIONS
# --------------------------------------------------


async def list_departments(
    db: AsyncSession,
    college: str,
    include_inactive: bool = False,
) -> list[DepartmentResponse]:
    normalized_college = normalize_college(college)

    statement = select(Department).where(
        _college_matches(
            Department.college,
            normalized_college,
        )
    )

    if not include_inactive:
        statement = statement.where(
            Department.is_active.is_(True)
        )

    statement = statement.order_by(
        Department.name.asc(),
        Department.id.asc(),
    )

    result = await db.execute(statement)
    departments = result.scalars().all()

    return [
        DepartmentResponse.model_validate(department)
        for department in departments
    ]


async def get_department(
    db: AsyncSession,
    department_id: int,
    college: str,
) -> DepartmentResponse:
    department = await _find_department(
        db,
        department_id,
        college,
    )

    if department is None:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    return DepartmentResponse.model_validate(department)


# --------------------------------------------------
# DEPARTMENT WRITE OPERATIONS
# --------------------------------------------------


async def create_department(
    db: AsyncSession,
    payload: DepartmentCreateRequest,
    *,
    created_by_admin_id: int | None = None,
    created_by_faculty_id: int | None = None,
) -> DepartmentResponse:
    normalized_college = normalize_college(payload.college)

    department = Department(
        college=normalized_college,
        name=payload.name,
        code=payload.code,
        is_active=payload.is_active,
        created_by_admin_id=created_by_admin_id,
        created_by_faculty_id=created_by_faculty_id,
    )

    db.add(department)

    await _commit_or_raise_conflict(
        db,
        (
            "A department with the same name or code "
            "already exists for this college"
        ),
    )

    await db.refresh(department)

    return DepartmentResponse.model_validate(department)


async def update_department(
    db: AsyncSession,
    department_id: int,
    college: str,
    payload: DepartmentUpdateRequest,
) -> DepartmentResponse:
    department = await _find_department(
        db,
        department_id,
        college,
    )

    if department is None:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    updates = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    for field_name, value in updates.items():
        setattr(department, field_name, value)

    await _commit_or_raise_conflict(
        db,
        (
            "A department with the same name or code "
            "already exists for this college"
        ),
    )

    await db.refresh(department)

    return DepartmentResponse.model_validate(department)