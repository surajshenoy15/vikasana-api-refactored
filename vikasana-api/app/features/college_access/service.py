from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.college_access.models import CollegeAccessControl
from app.features.organization.models import (
    College,
    CollegeAlias,
)


def _norm_college(college: str) -> str:
    return (college or "").strip()


def _college_key(college: str) -> str:
    return _norm_college(college).lower()


async def _resolve_college_scope_names(
    db: AsyncSession,
    college: str,
) -> list[str]:
    """
    Resolve a supplied college name or alias into the canonical
    College name plus all of its aliases.

    If the College master no longer exists, return the supplied name.
    This allows retained CollegeAccessControl tombstones to continue
    blocking users belonging to a deleted College.
    """
    college = _norm_college(college)

    if not college:
        return []

    normalized = _college_key(college)

    result = await db.execute(
        select(College).where(
            func.lower(
                func.trim(College.name)
            ) == normalized
        )
    )

    master = result.scalars().first()

    if master is None:
        alias_result = await db.execute(
            select(College)
            .join(
                CollegeAlias,
                CollegeAlias.college_id == College.id,
            )
            .where(
                func.lower(
                    func.trim(CollegeAlias.alias)
                ) == normalized
            )
        )

        master = alias_result.scalars().first()

    if master is None:
        return [college]

    aliases_result = await db.execute(
        select(CollegeAlias.alias).where(
            CollegeAlias.college_id == master.id
        )
    )

    aliases = aliases_result.scalars().all()

    names = [
        master.name,
        *aliases,
    ]

    unique_names: list[str] = []
    seen: set[str] = set()

    for name in names:
        cleaned = _norm_college(name)

        if not cleaned:
            continue

        key = cleaned.lower()

        if key in seen:
            continue

        seen.add(key)
        unique_names.append(cleaned)

    return unique_names


async def _find_access_row(
    db: AsyncSession,
    college: str,
) -> CollegeAccessControl | None:
    normalized = _college_key(college)

    result = await db.execute(
        select(CollegeAccessControl).where(
            func.lower(
                func.trim(
                    CollegeAccessControl.college
                )
            ) == normalized
        )
    )

    return result.scalars().first()


async def get_college_access_status(
    db: AsyncSession,
    college: str,
) -> dict:
    college = _norm_college(college)

    if not college:
        raise HTTPException(
            status_code=400,
            detail="college is required",
        )

    scope_names = await _resolve_college_scope_names(
        db,
        college,
    )

    normalized_names = [
        _college_key(name)
        for name in scope_names
    ]

    result = await db.execute(
        select(CollegeAccessControl).where(
            func.lower(
                func.trim(
                    CollegeAccessControl.college
                )
            ).in_(normalized_names)
        )
    )

    rows = result.scalars().all()

    inactive_rows = [
        row
        for row in rows
        if not row.is_active
    ]

    canonical_name = (
        scope_names[0]
        if scope_names
        else college
    )

    if inactive_rows:
        row = sorted(
            inactive_rows,
            key=lambda item: (
                item.deactivated_at
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
            reverse=True,
        )[0]

        return {
            "college": canonical_name,
            "is_active": False,
            "reason": row.reason,
            "deactivated_at": row.deactivated_at,
        }

    return {
        "college": canonical_name,
        "is_active": True,
        "reason": None,
        "deactivated_at": None,
    }


async def deactivate_college_access(
    db: AsyncSession,
    college: str,
    reason: str | None = None,
) -> dict:
    college = _norm_college(college)

    if not college:
        raise HTTPException(
            status_code=400,
            detail="college is required",
        )

    scope_names = await _resolve_college_scope_names(
        db,
        college,
    )

    now_utc = datetime.now(timezone.utc)

    final_reason = (
        reason
        or "Subscription inactive / payment pending"
    ).strip()

    for scope_name in scope_names:
        row = await _find_access_row(
            db,
            scope_name,
        )

        if row is None:
            row = CollegeAccessControl(
                college=scope_name,
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

    canonical_name = (
        scope_names[0]
        if scope_names
        else college
    )

    return {
        "college": canonical_name,
        "is_active": False,
        "reason": final_reason,
        "deactivated_at": now_utc,
    }


async def activate_college_access(
    db: AsyncSession,
    college: str,
) -> dict:
    college = _norm_college(college)

    if not college:
        raise HTTPException(
            status_code=400,
            detail="college is required",
        )

    scope_names = await _resolve_college_scope_names(
        db,
        college,
    )

    now_utc = datetime.now(timezone.utc)

    for scope_name in scope_names:
        row = await _find_access_row(
            db,
            scope_name,
        )

        if row is None:
            row = CollegeAccessControl(
                college=scope_name,
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

    canonical_name = (
        scope_names[0]
        if scope_names
        else college
    )

    return {
        "college": canonical_name,
        "is_active": True,
        "reason": None,
        "deactivated_at": None,
    }


async def ensure_college_is_active(
    db: AsyncSession,
    college: str | None,
) -> None:
    college = _norm_college(
        college or ""
    )

    if not college:
        return

    scope_names = await _resolve_college_scope_names(
        db,
        college,
    )

    normalized_names = [
        _college_key(name)
        for name in scope_names
    ]

    result = await db.execute(
        select(CollegeAccessControl).where(
            func.lower(
                func.trim(
                    CollegeAccessControl.college
                )
            ).in_(normalized_names),
            CollegeAccessControl.is_active.is_(False),
        )
    )

    blocked_row = result.scalars().first()

    if blocked_row:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access for college '{college}' has been "
                "deactivated. Contact support."
            ),
        )
