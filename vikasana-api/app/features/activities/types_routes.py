# app/routes/activity_types.py

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.activities.models import ActivityType, ActivityTypeStatus
from app.features.activities.schemas.activity_type import (
    ActivityTypeCreate,
    ActivityTypeUpdate,
    ActivityTypeOut,
)

# ✅ IMPORTANT: no /api here because main.py already adds /api
router = APIRouter(prefix="/activity-types", tags=["Activity Types"])


# ─────────────────────────────────────────────────────────────
# Public (used by frontend dropdown)
# ─────────────────────────────────────────────────────────────
@router.get("", response_model=list[ActivityTypeOut])
async def list_activity_types(
    active_only: bool = Query(True, description="If true, only active types"),
    approved_only: bool = Query(True, description="If true, only APPROVED types"),
    q: str | None = Query(None, description="Search by name"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ActivityType)

    if active_only:
        stmt = stmt.where(ActivityType.is_active == True)

    if approved_only:
        stmt = stmt.where(ActivityType.status == ActivityTypeStatus.APPROVED)

    if q:
        stmt = stmt.where(ActivityType.name.ilike(f"%{q.strip()}%"))

    stmt = stmt.order_by(ActivityType.name.asc())

    res = await db.execute(stmt)
    return res.scalars().all()


# ─────────────────────────────────────────────────────────────
# Admin CRUD
# ─────────────────────────────────────────────────────────────
@router.post("", response_model=ActivityTypeOut)
async def create_activity_type(
    payload: ActivityTypeCreate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    name = payload.name.strip()

    existing = await db.execute(select(ActivityType).where(ActivityType.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Activity type already exists")

    row = ActivityType(
        name=name,
        description=payload.description,
        status=payload.status,
        hours_per_unit=payload.hours_per_unit,
        points_per_unit=payload.points_per_unit,
        max_points=payload.max_points,
        maps_url=payload.maps_url,
        target_lat=payload.target_lat,
        target_lng=payload.target_lng,
        radius_m=payload.radius_m,
        is_active=payload.is_active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/{activity_type_id}", response_model=ActivityTypeOut)
async def get_activity_type(
    activity_type_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    res = await db.execute(select(ActivityType).where(ActivityType.id == activity_type_id))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.patch("/{activity_type_id}", response_model=ActivityTypeOut)
async def update_activity_type(
    activity_type_id: int,
    payload: ActivityTypeUpdate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    res = await db.execute(select(ActivityType).where(ActivityType.id == activity_type_id))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    # If name is updated, keep uniqueness
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name != row.name:
            existing = await db.execute(select(ActivityType).where(ActivityType.name == new_name))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Activity type name already exists")
        row.name = new_name

    if payload.description is not None:
        row.description = payload.description

    if payload.status is not None:
        row.status = payload.status

    if payload.hours_per_unit is not None:
        row.hours_per_unit = payload.hours_per_unit

    if payload.points_per_unit is not None:
        row.points_per_unit = payload.points_per_unit

    if payload.max_points is not None:
        row.max_points = payload.max_points

    # Geofence (allow clearing by sending null)
    if payload.maps_url is not None:
        row.maps_url = payload.maps_url
    if payload.target_lat is not None:
        row.target_lat = payload.target_lat
    if payload.target_lng is not None:
        row.target_lng = payload.target_lng
    if payload.radius_m is not None:
        row.radius_m = payload.radius_m

    if payload.is_active is not None:
        row.is_active = payload.is_active

    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{activity_type_id}")
async def deactivate_activity_type(
    activity_type_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    res = await db.execute(select(ActivityType).where(ActivityType.id == activity_type_id))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    row.is_active = False
    await db.commit()
    return {"ok": True}