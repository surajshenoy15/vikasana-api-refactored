from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.college_access.models import CollegeAccessControl


def _norm_college(college: str) -> str:
    return (college or "").strip()


async def get_college_access_status(db: AsyncSession, college: str) -> dict:
    college = _norm_college(college)
    if not college:
        raise HTTPException(status_code=400, detail="college is required")

    q = await db.execute(
        select(CollegeAccessControl).where(CollegeAccessControl.college == college)
    )
    row = q.scalar_one_or_none()

    if not row:
        return {
            "college": college,
            "is_active": True,
            "reason": None,
            "deactivated_at": None,
        }

    return {
        "college": row.college,
        "is_active": bool(row.is_active),
        "reason": row.reason,
        "deactivated_at": row.deactivated_at,
    }


async def deactivate_college_access(db: AsyncSession, college: str, reason: str | None = None) -> dict:
    college = _norm_college(college)
    if not college:
        raise HTTPException(status_code=400, detail="college is required")

    q = await db.execute(
        select(CollegeAccessControl).where(CollegeAccessControl.college == college)
    )
    row = q.scalar_one_or_none()

    now_utc = datetime.now(timezone.utc)
    final_reason = (reason or "Subscription inactive / payment pending").strip()

    if row is None:
        row = CollegeAccessControl(
            college=college,
            is_active=False,
            reason=final_reason,
            deactivated_at=now_utc,
        )
        db.add(row)
    else:
        row.is_active = False
        row.reason = final_reason
        row.deactivated_at = now_utc
        row.updated_at = now_utc

    await db.commit()
    await db.refresh(row)

    return {
        "college": row.college,
        "is_active": bool(row.is_active),
        "reason": row.reason,
        "deactivated_at": row.deactivated_at,
    }


async def activate_college_access(db: AsyncSession, college: str) -> dict:
    college = _norm_college(college)
    if not college:
        raise HTTPException(status_code=400, detail="college is required")

    q = await db.execute(
        select(CollegeAccessControl).where(CollegeAccessControl.college == college)
    )
    row = q.scalar_one_or_none()

    now_utc = datetime.now(timezone.utc)

    if row is None:
        row = CollegeAccessControl(
            college=college,
            is_active=True,
            reason=None,
            deactivated_at=None,
        )
        db.add(row)
    else:
        row.is_active = True
        row.reason = None
        row.deactivated_at = None
        row.updated_at = now_utc

    await db.commit()
    await db.refresh(row)

    return {
        "college": row.college,
        "is_active": bool(row.is_active),
        "reason": row.reason,
        "deactivated_at": row.deactivated_at,
    }


async def ensure_college_is_active(db: AsyncSession, college: str | None) -> None:
    college = _norm_college(college or "")
    if not college:
        return

    q = await db.execute(
        select(CollegeAccessControl).where(CollegeAccessControl.college == college)
    )
    row = q.scalar_one_or_none()

    if row and not row.is_active:
        raise HTTPException(
            status_code=403,
            detail=f"Access for college '{college}' has been deactivated. Contact support.",
        )