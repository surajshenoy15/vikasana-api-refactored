from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy import (
    asc,
    desc,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.features.auth.models import Admin
from app.features.audit.models import AuditLog
from app.features.audit.schemas import AuditLogListResponse
from app.features.audit.service import VALID_ACTOR_TYPES


router = APIRouter(
    prefix="/admin/audit-logs",
    tags=["Admin - Audit Logs"],
)


@router.get(
    "",
    response_model=AuditLogListResponse,
    summary="List immutable audit logs",
)
async def list_audit_logs(
    actor_type: str | None = Query(
        default=None,
        max_length=40,
    ),
    action: str | None = Query(
        default=None,
        max_length=100,
    ),
    college: str | None = Query(
        default=None,
        max_length=200,
    ),
    department_id: int | None = Query(
        default=None,
        ge=1,
    ),
    search: str | None = Query(
        default=None,
        max_length=255,
    ),
    start_at: datetime | None = Query(
        default=None,
    ),
    end_at: datetime | None = Query(
        default=None,
    ),
    sort: Literal[
        "newest",
        "oldest",
    ] = Query(
        default="newest",
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        get_current_admin
    ),
) -> AuditLogListResponse:
    del current_admin

    filters = []

    # --------------------------------------------------
    # ACTOR TYPE
    # --------------------------------------------------

    if actor_type:
        normalized_actor_type = (
            actor_type
            .strip()
            .upper()
        )

        if (
            normalized_actor_type
            not in VALID_ACTOR_TYPES
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid actor_type. "
                    "Allowed values: "
                    + ", ".join(
                        sorted(
                            VALID_ACTOR_TYPES
                        )
                    )
                ),
            )

        filters.append(
            AuditLog.actor_type
            == normalized_actor_type
        )

    # --------------------------------------------------
    # ACTION
    # --------------------------------------------------

    if action:
        normalized_action = (
            action
            .strip()
            .upper()
        )

        if normalized_action:
            filters.append(
                AuditLog.action
                == normalized_action
            )

    # --------------------------------------------------
    # ORGANIZATION
    # --------------------------------------------------

    if college:
        normalized_college = (
            college
            .strip()
            .casefold()
        )

        if normalized_college:
            filters.append(
                func.lower(
                    func.trim(
                        AuditLog.college
                    )
                )
                == normalized_college
            )

    if department_id is not None:
        filters.append(
            AuditLog.department_id
            == department_id
        )

    # --------------------------------------------------
    # DATE RANGE
    # --------------------------------------------------

    if start_at is not None:
        filters.append(
            AuditLog.created_at
            >= start_at
        )

    if end_at is not None:
        filters.append(
            AuditLog.created_at
            <= end_at
        )

    if (
        start_at is not None
        and end_at is not None
        and start_at > end_at
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "start_at cannot be later "
                "than end_at"
            ),
        )

    # --------------------------------------------------
    # SEARCH
    # --------------------------------------------------

    if search:
        normalized_search = (
            search
            .strip()
        )

        if normalized_search:
            pattern = (
                f"%{normalized_search}%"
            )

            filters.append(
                or_(
                    AuditLog.actor_name.ilike(
                        pattern
                    ),
                    AuditLog.actor_identifier.ilike(
                        pattern
                    ),
                    AuditLog.actor_email.ilike(
                        pattern
                    ),
                    AuditLog.action.ilike(
                        pattern
                    ),
                    AuditLog.description.ilike(
                        pattern
                    ),
                    AuditLog.college.ilike(
                        pattern
                    ),
                    AuditLog.department_name.ilike(
                        pattern
                    ),
                    AuditLog.entity_type.ilike(
                        pattern
                    ),
                )
            )

    # --------------------------------------------------
    # TOTAL
    # --------------------------------------------------

    total_stmt = (
        select(
            func.count(
                AuditLog.id
            )
        )
        .select_from(
            AuditLog
        )
    )

    if filters:
        total_stmt = total_stmt.where(
            *filters
        )

    total = int(
        (
            await db.execute(
                total_stmt
            )
        ).scalar_one()
        or 0
    )

    # --------------------------------------------------
    # ROWS
    # --------------------------------------------------

    order_expression = (
        desc(
            AuditLog.created_at
        )
        if sort == "newest"
        else asc(
            AuditLog.created_at
        )
    )

    stmt = (
        select(
            AuditLog
        )
        .order_by(
            order_expression,
            (
                desc(
                    AuditLog.id
                )
                if sort == "newest"
                else asc(
                    AuditLog.id
                )
            ),
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
    )

    if filters:
        stmt = stmt.where(
            *filters
        )

    result = await db.execute(
        stmt
    )

    items = list(
        result.scalars().all()
    )

    return AuditLogListResponse(
        total=total,
        offset=offset,
        limit=limit,
        items=items,
    )
