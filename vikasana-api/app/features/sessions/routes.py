# app/routes/admin_sessions.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_admin

from app.features.activities.models import ActivitySessionStatus

from app.features.sessions.service import (
    admin_list_sessions,
    admin_get_session_detail,
    admin_approve_session,
    admin_reject_session,
)

router = APIRouter(
    prefix="/admin/sessions",
    tags=["Admin - Sessions"],
)

# ─────────────────────────────────────────────────────────────
# LIST SESSIONS
# ─────────────────────────────────────────────────────────────

@router.get("")
async def list_sessions(
    status: Optional[str] = Query(
        None,
        description=(
            "Filter by status. "
            "Default queue = SUBMITTED + FLAGGED. "
            "Use 'ALL' to disable status filter."
        ),
    ),
    q: Optional[str] = Query(
        None,
        description="Search by activity name or session code",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    parsed_status: Optional[ActivitySessionStatus] = None
    include_all = False

    if status:
        s = status.upper().strip()

        if s == "ALL":
            include_all = True
        else:
            try:
                parsed_status = ActivitySessionStatus(s)
            except ValueError:
                parsed_status = None

    return await admin_list_sessions(
        db=db,
        status=parsed_status,
        include_all=include_all,
        q=q,
        limit=limit,
        offset=offset,
    )


# ─────────────────────────────────────────────────────────────
# SESSION DETAIL
# ─────────────────────────────────────────────────────────────

@router.get("/{session_id}")
async def get_session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """
    Returns full session detail including:

    • student info  
    • activity type details  
    • session timestamps  
    • activity photos  
    • GPS trail  
    • face verification results  
    """

    return await admin_get_session_detail(
        db=db,
        session_id=session_id,
    )


# ─────────────────────────────────────────────────────────────
# APPROVE SESSION
# ─────────────────────────────────────────────────────────────

@router.post("/{session_id}/approve")
async def approve_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """
    Approves activity session and awards points.
    Admin id is stored in StudentPointAdjustment table.
    """

    return await admin_approve_session(
        db=db,
        session_id=session_id,
        current_admin_id=getattr(admin, "id", None),
    )


# ─────────────────────────────────────────────────────────────
# REJECT SESSION
# ─────────────────────────────────────────────────────────────

class RejectBody(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        description="Reason shown to student",
    )


@router.post("/{session_id}/reject")
async def reject_session(
    session_id: int,
    payload: RejectBody,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    session = await admin_reject_session(
        db=db,
        session_id=session_id,
        reason=payload.reason,
    )

    return {
        "id": session.id,
        "status": session.status.value
        if hasattr(session.status, "value")
        else str(session.status),
        "flag_reason": session.flag_reason,
    }