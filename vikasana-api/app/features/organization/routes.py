from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.auth.models import Admin
from app.features.organization.schemas import (
    AcademicBatchCreateRequest,
    AcademicBatchResponse,
    AcademicBatchUpdateRequest,
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
)
from app.features.organization.service import (
    create_academic_batch,
    create_department,
    get_academic_batch,
    get_department,
    get_organization_settings,
    list_academic_batches,
    list_departments,
    update_academic_batch,
    update_department,
    update_organization_settings,
)


admin_router = APIRouter(
    prefix="/admin/organization",
    tags=["Admin - Organization"],
)


# --------------------------------------------------
# ORGANISATION SETTINGS
# --------------------------------------------------


@admin_router.get(
    "/settings",
    response_model=OrganizationSettingsResponse,
)
async def read_organization_settings(
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await get_organization_settings(
        db,
        college,
    )


@admin_router.put(
    "/settings",
    response_model=OrganizationSettingsResponse,
)
async def write_organization_settings(
    payload: OrganizationSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    # Department mode must remain disabled during staged development.
    if payload.department_architecture_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Department architecture cannot be enabled "
                "during the staged rollout"
            ),
        )

    return await update_organization_settings(
        db,
        payload,
    )


# --------------------------------------------------
# DEPARTMENT ROUTES
# --------------------------------------------------


@admin_router.get(
    "/departments",
    response_model=list[DepartmentResponse],
)
async def read_departments(
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await list_departments(
        db,
        college,
        include_inactive,
    )


@admin_router.get(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
)
async def read_department(
    department_id: int,
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await get_department(
        db,
        department_id,
        college,
    )


@admin_router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def write_department(
    payload: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await create_department(
        db,
        payload,
        created_by_admin_id=current_admin.id,
    )


@admin_router.patch(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
)
async def edit_department(
    department_id: int,
    payload: DepartmentUpdateRequest,
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await update_department(
        db,
        department_id,
        college,
        payload,
    )


# --------------------------------------------------
# ACADEMIC BATCH ROUTES
# --------------------------------------------------


@admin_router.get(
    "/batches",
    response_model=list[AcademicBatchResponse],
)
async def read_academic_batches(
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await list_academic_batches(
        db,
        college,
        include_inactive,
    )


@admin_router.get(
    "/batches/{batch_id}",
    response_model=AcademicBatchResponse,
)
async def read_academic_batch(
    batch_id: int,
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await get_academic_batch(
        db,
        batch_id,
        college,
    )


@admin_router.post(
    "/batches",
    response_model=AcademicBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def write_academic_batch(
    payload: AcademicBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await create_academic_batch(
        db,
        payload,
        created_by_admin_id=current_admin.id,
    )


@admin_router.patch(
    "/batches/{batch_id}",
    response_model=AcademicBatchResponse,
)
async def edit_academic_batch(
    batch_id: int,
    payload: AcademicBatchUpdateRequest,
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await update_academic_batch(
        db,
        batch_id,
        college,
        payload,
    )