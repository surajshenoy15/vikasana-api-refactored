from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)
from sqlalchemy import (
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.jwt import decode_access_token
from app.core.dependencies import require_super_admin
from app.features.auth.models import Admin
from app.features.audit.models import AuditLog
from app.features.audit.schemas import AuditLogListResponse
from app.features.super_admin.schemas import (
    AdminAccountListResponse,
    AdminAccountOut,
    AdminCreateRequest,
    AdminUpdateRequest,
    AdminSessionListResponse,
    PasswordChangeResponse,
    SuperAdminPasswordChangeRequest,
    AdminSessionOut,
    AdminSessionsRevokedResponse,
    SuperAdminMeOut,
    SuperAdminPasswordOtpRequest,
    SuperAdminPasswordOtpStartResponse,
    SuperAdminPasswordOtpVerifyRequest,
)
from app.features.super_admin.service import (
    activate_admin_account,
    create_admin_account,
    change_super_admin_password,
    deactivate_admin_account,
    get_super_admin_me,
    update_admin_account,
    list_admin_accounts,
    list_admin_sessions,
    revoke_admin_device_session,
    revoke_all_admin_sessions,
    request_super_admin_password_otp,
    verify_super_admin_password_otp,
)


router = APIRouter(
    prefix="/super-admin",
    tags=["Super Admin"],
)


def _current_session_id(
    request: Request,
) -> str | None:
    """
    Extract the authenticated Admin session id from the
    already-validated Bearer token.

    Super Admin routes are still protected by
    require_super_admin; this helper is only used to mark
    the current device in session-list responses.
    """

    authorization = (
        request.headers.get(
            "authorization"
        )
        or ""
    ).strip()

    if not authorization.lower().startswith(
        "bearer "
    ):
        return None

    token = authorization.split(
        " ",
        1,
    )[1].strip()

    if not token:
        return None

    try:
        payload = decode_access_token(
            token
        )
    except Exception:
        return None

    session_id = payload.get(
        "sid"
    )

    return (
        str(session_id)
        if session_id
        else None
    )


@router.get(
    "/audit-logs",
    response_model=AuditLogListResponse,
    summary="List immutable audit logs for Super Admin",
)
async def super_admin_list_audit_logs(
    search: str | None = Query(
        default=None,
        max_length=255,
    ),
    action: str | None = Query(
        default=None,
        max_length=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AuditLogListResponse:
    del current_admin

    filters = []

    if action:
        normalized_action = (
            action.strip().upper()
        )

        if normalized_action:
            filters.append(
                AuditLog.action
                == normalized_action
            )

    if search:
        normalized_search = (
            search.strip()
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
                    AuditLog.actor_email.ilike(
                        pattern
                    ),
                    AuditLog.actor_role.ilike(
                        pattern
                    ),
                    AuditLog.action.ilike(
                        pattern
                    ),
                    AuditLog.description.ilike(
                        pattern
                    ),
                    AuditLog.entity_type.ilike(
                        pattern
                    ),
                    AuditLog.ip_address.ilike(
                        pattern
                    ),
                )
            )

    count_stmt = select(
        func.count(
            AuditLog.id
        )
    )

    rows_stmt = (
        select(AuditLog)
        .order_by(
            AuditLog.created_at.desc(),
            AuditLog.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    if filters:
        count_stmt = count_stmt.where(
            *filters
        )

        rows_stmt = rows_stmt.where(
            *filters
        )

    total = int(
        (
            await db.execute(
                count_stmt
            )
        ).scalar_one()
        or 0
    )

    items = list(
        (
            await db.execute(
                rows_stmt
            )
        ).scalars().all()
    )

    return AuditLogListResponse(
        total=total,
        offset=offset,
        limit=limit,
        items=items,
    )


@router.get(
    "/me",
    response_model=SuperAdminMeOut,
    summary="Get current Super Admin profile",
)
async def super_admin_me(
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> SuperAdminMeOut:
    return await get_super_admin_me(
        current_admin
    )


@router.post(
    "/me/change-password/request-otp",
    response_model=SuperAdminPasswordOtpStartResponse,
    summary="Request fresh OTP for Super Admin password change",
)
async def super_admin_request_password_otp(
    payload: SuperAdminPasswordOtpRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> SuperAdminPasswordOtpStartResponse:
    return await request_super_admin_password_otp(
        db,
        payload=payload,
        actor=current_admin,
        request=request,
    )


@router.post(
    "/me/change-password/verify",
    response_model=PasswordChangeResponse,
    summary="Verify OTP and change Super Admin password",
)
async def super_admin_verify_password_otp(
    payload: SuperAdminPasswordOtpVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> PasswordChangeResponse:
    return await verify_super_admin_password_otp(
        db,
        payload=payload,
        actor=current_admin,
        request=request,
    )


@router.post(
    "/admins",
    response_model=AdminAccountOut,
    status_code=201,
    summary="Create an Admin account",
)
async def super_admin_create_admin(
    payload: AdminCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminAccountOut:
    return await create_admin_account(
        db,
        payload=payload,
        actor=current_admin,
        request=request,
    )


@router.get(
    "/admins",
    response_model=AdminAccountListResponse,
    summary="List LoRaa Connect Admin accounts",
)
async def super_admin_list_admins(
    search: str | None = Query(
        default=None,
        max_length=255,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminAccountListResponse:
    del current_admin

    return await list_admin_accounts(
        db,
        offset=offset,
        limit=limit,
        search=search,
    )


@router.patch(
    "/admins/{admin_id}",
    response_model=AdminAccountOut,
    summary="Edit an Admin account",
)
async def super_admin_update_admin(
    admin_id: int,
    payload: AdminUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminAccountOut:
    return await update_admin_account(
        db,
        target_admin_id=admin_id,
        payload=payload,
        actor=current_admin,
        request=request,
    )


@router.post(
    "/admins/{admin_id}/activate",
    response_model=AdminAccountOut,
    summary="Activate an Admin account",
)
async def super_admin_activate_admin(
    admin_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminAccountOut:
    return await activate_admin_account(
        db,
        target_admin_id=admin_id,
        actor=current_admin,
        request=request,
    )


@router.post(
    "/admins/{admin_id}/deactivate",
    response_model=AdminAccountOut,
    summary="Deactivate an Admin account",
)
async def super_admin_deactivate_admin(
    admin_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminAccountOut:
    return await deactivate_admin_account(
        db,
        target_admin_id=admin_id,
        actor=current_admin,
        request=request,
    )


@router.get(
    "/admins/{admin_id}/sessions",
    response_model=AdminSessionListResponse,
    summary="List Admin device sessions",
)
async def super_admin_list_admin_sessions(
    admin_id: int,
    request: Request,
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminSessionListResponse:
    del current_admin

    return await list_admin_sessions(
        db,
        target_admin_id=admin_id,
        offset=offset,
        limit=limit,
        current_session_id=(
            _current_session_id(request)
        ),
    )


@router.post(
    "/sessions/{session_id}/revoke",
    response_model=AdminSessionOut,
    summary="Revoke one Admin device session",
)
async def super_admin_revoke_admin_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminSessionOut:
    return await revoke_admin_device_session(
        db,
        session_id=session_id,
        actor=current_admin,
        request=request,
        current_session_id=(
            _current_session_id(request)
        ),
    )


@router.post(
    "/admins/{admin_id}/revoke-all-sessions",
    response_model=AdminSessionsRevokedResponse,
    summary="Revoke all Admin device sessions",
)
async def super_admin_revoke_all_admin_sessions(
    admin_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: Admin = Depends(
        require_super_admin
    ),
) -> AdminSessionsRevokedResponse:
    return await revoke_all_admin_sessions(
        db,
        target_admin_id=admin_id,
        actor=current_admin,
        request=request,
    )
