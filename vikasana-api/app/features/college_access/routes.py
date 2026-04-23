from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.auth.models import Admin
from app.features.college_access.schemas import (
    CollegeAccessToggleRequest,
    CollegeAccessStatusResponse,
)
from app.features.college_access.service import (
    get_college_access_status,
    deactivate_college_access,
    activate_college_access,
)

router = APIRouter(prefix="/college-access", tags=["College Access"])


@router.get("/status", response_model=CollegeAccessStatusResponse)
async def college_access_status(
    college: str = Query(...),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    return await get_college_access_status(db, college)


@router.post("/deactivate", response_model=CollegeAccessStatusResponse)
async def college_access_deactivate(
    payload: CollegeAccessToggleRequest,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    return await deactivate_college_access(db, payload.college, payload.reason)


@router.post("/activate", response_model=CollegeAccessStatusResponse)
async def college_access_activate(
    payload: CollegeAccessToggleRequest,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    return await activate_college_access(db, payload.college)