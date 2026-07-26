from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.organization.models import CollegeOrganizationSetting
from app.features.organization.schemas import (
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


async def _find_organization_settings(
    db: AsyncSession,
    college: str,
) -> CollegeOrganizationSetting | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(CollegeOrganizationSetting).where(
            func.lower(
                func.trim(CollegeOrganizationSetting.college)
            )
            == normalized_college.lower()
        )
    )

    return result.scalar_one_or_none()


# --------------------------------------------------
# ORGANISATION SETTINGS
# --------------------------------------------------


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

    await db.commit()
    await db.refresh(settings)

    return OrganizationSettingsResponse.model_validate(settings)