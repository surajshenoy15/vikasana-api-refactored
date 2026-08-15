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
    CollegeCreateRequest,
    CollegeDetailsResponse,
    CollegeResponse,
    CollegeUpdateRequest,
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
)
from app.features.organization.service import (
    delete_college_master,
    create_academic_batch,
    create_college,
    create_department,
    delete_department,
    get_academic_batch,
    get_college,
    get_college_details,
    get_department,
    get_organization_settings,
    list_academic_batches,
    list_colleges,
    list_departments,
    update_academic_batch,
    update_college,
    update_department,
    update_organization_settings,
)


admin_router = APIRouter(
    prefix="/admin/organization",
    tags=["Admin - Organization"],
)


# --------------------------------------------------
# COLLEGE MASTER ROUTES
# --------------------------------------------------


@admin_router.get(
    "/colleges",
    response_model=list[CollegeResponse],
)
async def read_colleges(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await list_colleges(
        db,
        include_inactive=include_inactive,
    )


@admin_router.get(
    "/colleges/{college_id}",
    response_model=CollegeDetailsResponse,
)
async def read_college(
    college_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await get_college_details(
        db,
        college_id,
    )


@admin_router.post(
    "/colleges",
    response_model=CollegeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def write_college(
    payload: CollegeCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await create_college(
        db,
        payload,
        created_by_admin_id=current_admin.id,
    )


@admin_router.patch(
    "/colleges/{college_id}",
    response_model=CollegeResponse,
)
async def edit_college(
    college_id: int,
    payload: CollegeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    return await update_college(
        db,
        college_id,
        payload,
    )


@admin_router.delete(
    "/colleges/{college_id}",
    response_model=CollegeResponse,
)
async def delete_college(
    college_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    """
    Delete the College master record while preserving linked
    historical hierarchy and operational records.
    """
    return await delete_college_master(
        db,
        college_id,
        deleted_by_admin_id=current_admin.id,
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


@admin_router.delete(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
)
async def remove_department(
    department_id: int,
    college: str = Query(
        ...,
        min_length=1,
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin),
):
    """
    Permanently delete an unused Department.

    Departments referenced by Faculty, Students, or hierarchy
    history are protected and must be deactivated instead.
    """
    return await delete_department(
        db,
        department_id,
        college,
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