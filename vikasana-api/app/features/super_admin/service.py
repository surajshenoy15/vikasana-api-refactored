from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.models import Admin, AdminMFAOtp, AdminSession
from app.features.super_admin.schemas import (
    AdminAccountListResponse,
    AdminAccountOut,
    AdminCreateRequest,
    AdminUpdateRequest,
    PasswordChangeResponse,
    SuperAdminMeOut,
    SuperAdminPasswordChangeRequest,
    SuperAdminPasswordOtpRequest,
    SuperAdminPasswordOtpStartResponse,
    SuperAdminPasswordOtpVerifyRequest,
)


async def get_super_admin_me(
    admin: Admin,
) -> SuperAdminMeOut:
    return SuperAdminMeOut.model_validate(admin)


async def list_admin_accounts(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
    search: str | None = None,
) -> AdminAccountListResponse:
    filters = []

    normalized_search = (
        search.strip()
        if search
        else ""
    )

    if normalized_search:
        pattern = f"%{normalized_search}%"

        filters.append(
            or_(
                Admin.name.ilike(pattern),
                Admin.email.ilike(pattern),
            )
        )

    count_stmt = select(
        func.count(Admin.id)
    )

    list_stmt = (
        select(Admin)
        .order_by(
            Admin.id.desc()
        )
        .offset(offset)
        .limit(limit)
    )

    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    total = (
        await db.execute(
            count_stmt
        )
    ).scalar_one()

    rows = (
        await db.execute(
            list_stmt
        )
    ).scalars().all()

    return AdminAccountListResponse(
        items=[
            AdminAccountOut.model_validate(row)
            for row in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


async def create_admin_account(
    db: AsyncSession,
    *,
    payload: AdminCreateRequest,
    actor: Admin,
    request=None,
) -> AdminAccountOut:
    from fastapi import HTTPException, status

    from app.core.security import hash_password
    from app.features.audit.service import append_audit_log

    name = payload.name.strip()
    email = payload.email.strip().lower()
    password = payload.password

    if len(name) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Admin name must contain at least 2 characters",
        )

    if (
        "@" not in email
        or "." not in email.rsplit("@", 1)[-1]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid email address",
        )

    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must contain at least 8 characters",
        )

    existing = (
        await db.execute(
            select(Admin).where(
                func.lower(Admin.email) == email
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An Admin account with this email already exists",
        )

    target = Admin(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role="admin",
        token_version=1,
        is_active=True,
    )

    db.add(target)

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="ADMIN_CREATED",
        description=(
            f"{actor.name} created admin account "
            f"{target.email}."
        ),
        entity_type="admin",
        entity_id=target.id,
        source="super_admin_web",
        request=request,
        metadata={
            "target_admin_name": target.name,
            "target_admin_email": target.email,
            "target_admin_role": "admin",
            "is_active": True,
        },
        commit=False,
    )

    await db.commit()
    await db.refresh(target)

    return AdminAccountOut.model_validate(target)


async def _revoke_active_sessions_for_security_change(
    db: AsyncSession,
    *,
    target: Admin,
    actor: Admin,
    reason: str,
) -> int:
    from app.features.auth.admin_session_service import (
        revoke_admin_session,
    )

    sessions = (
        await db.execute(
            select(AdminSession).where(
                AdminSession.admin_id == target.id,
                AdminSession.status == "active",
            )
        )
    ).scalars().all()

    for session in sessions:
        await revoke_admin_session(
            db,
            session=session,
            revoked_by_admin_id=actor.id,
            reason=reason,
        )

    return len(sessions)


async def update_admin_account(
    db: AsyncSession,
    *,
    target_admin_id: int,
    payload: AdminUpdateRequest,
    actor: Admin,
    request=None,
) -> AdminAccountOut:
    from fastapi import HTTPException, status

    from app.core.security import hash_password
    from app.features.audit.service import append_audit_log

    target = (
        await db.execute(
            select(Admin).where(
                Admin.id == target_admin_id
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    name = payload.name.strip()
    email = payload.email.strip().lower()

    if (
        target.role == "super_admin"
        and email
        != target.email.strip().lower()
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Super Admin email cannot be changed "
                "from Admin Accounts"
            ),
        )

    if len(name) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Admin name must contain at least 2 characters",
        )

    if (
        "@" not in email
        or "." not in email.rsplit("@", 1)[-1]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid email address",
        )

    duplicate = (
        await db.execute(
            select(Admin).where(
                func.lower(Admin.email) == email,
                Admin.id != target.id,
            )
        )
    ).scalar_one_or_none()

    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another Admin account already uses this email",
        )

    new_password = payload.new_password

    if (
        new_password is not None
        and new_password != ""
    ):
        if target.role == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Change the Super Admin password from My Security",
            )

        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password must contain at least 8 characters",
            )

    old_name = target.name
    old_email = target.email
    old_token_version = int(
        target.token_version or 1
    )

    target.name = name
    target.email = email

    revoked_count = 0
    password_reset = bool(new_password)

    if password_reset:
        target.password_hash = hash_password(
            new_password
        )

        target.token_version = (
            old_token_version + 1
        )

        revoked_count = await _revoke_active_sessions_for_security_change(
            db,
            target=target,
            actor=actor,
            reason="Password reset by Super Admin",
        )

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="ADMIN_UPDATED",
        description=(
            f"{actor.name} updated admin account "
            f"{target.email}."
        ),
        entity_type="admin",
        entity_id=target.id,
        source="super_admin_web",
        request=request,
        metadata={
            "old_name": old_name,
            "new_name": target.name,
            "old_email": old_email,
            "new_email": target.email,
            "password_reset": password_reset,
            "revoked_sessions": revoked_count,
            "old_token_version": old_token_version,
            "new_token_version": target.token_version,
        },
        commit=False,
    )

    await db.commit()
    await db.refresh(target)

    return AdminAccountOut.model_validate(target)


async def request_super_admin_password_otp(
    db: AsyncSession,
    *,
    payload: SuperAdminPasswordOtpRequest,
    actor: Admin,
    request=None,
) -> SuperAdminPasswordOtpStartResponse:
    import secrets
    from datetime import datetime, timedelta, timezone

    from fastapi import HTTPException, status

    from app.core.security import verify_password
    from app.features.audit.models import AuditLog
    from app.features.audit.service import append_audit_log
    from app.features.auth.service import (
        _hash_otp,
        _send_admin_mfa_email,
    )

    now = datetime.now(timezone.utc)

    # --------------------------------------------------------
    # Rate-limit repeated wrong-current-password attempts.
    # Five failures within 10 minutes blocks further attempts.
    # --------------------------------------------------------

    failed_since = now - timedelta(
        minutes=10
    )

    failed_count = int(
        (
            await db.execute(
                select(
                    func.count(
                        AuditLog.id
                    )
                ).where(
                    AuditLog.actor_id
                    == actor.id,
                    AuditLog.actor_role
                    == "super_admin",
                    AuditLog.action
                    == "SUPER_ADMIN_PASSWORD_OTP_REQUEST_FAILED",
                    AuditLog.created_at
                    >= failed_since,
                )
            )
        ).scalar_one()
        or 0
    )

    if failed_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many failed security attempts. "
                "Try again later."
            ),
        )

    if not verify_password(
        payload.current_password,
        actor.password_hash,
    ):
        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role="super_admin",
            actor_name=actor.name,
            actor_identifier=str(
                actor.id
            ),
            actor_email=actor.email,
            action=(
                "SUPER_ADMIN_PASSWORD_OTP_REQUEST_FAILED"
            ),
            description=(
                "Super Admin password-change OTP request "
                "failed because the current password was incorrect."
            ),
            entity_type="admin",
            entity_id=actor.id,
            source="super_admin_web",
            request=request,
            metadata={
                "reason":
                    "incorrect_current_password",
            },
            commit=True,
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # --------------------------------------------------------
    # Prevent OTP-spam. One new challenge per minute.
    # --------------------------------------------------------

    latest = (
        await db.execute(
            select(
                AdminMFAOtp
            )
            .where(
                AdminMFAOtp.admin_id
                == actor.id,
                AdminMFAOtp.mfa_token.like(
                    "pwdchg_%"
                ),
            )
            .order_by(
                AdminMFAOtp.created_at.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if (
        latest is not None
        and not latest.used
        and latest.expires_at > now
        and latest.created_at is not None
        and latest.created_at
        > now - timedelta(seconds=60)
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "A security OTP was already sent. "
                "Please wait before requesting another."
            ),
        )

    otp = (
        f"{secrets.randbelow(1000000):06d}"
    )

    mfa_token = (
        "pwdchg_"
        + secrets.token_urlsafe(32)
    )

    otp_row = AdminMFAOtp(
        admin_id=actor.id,
        otp_hash=_hash_otp(otp),
        mfa_token=mfa_token,
        attempts=0,
        used=False,
        expires_at=(
            now
            + timedelta(minutes=5)
        ),
    )

    db.add(otp_row)

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role="super_admin",
        actor_name=actor.name,
        actor_identifier=str(
            actor.id
        ),
        actor_email=actor.email,
        action=(
            "SUPER_ADMIN_PASSWORD_OTP_REQUESTED"
        ),
        description=(
            "Super Admin requested a password-change security OTP."
        ),
        entity_type="admin",
        entity_id=actor.id,
        source="super_admin_web",
        request=request,
        metadata={
            "expires_in_seconds": 300,
        },
        commit=False,
    )

    await db.commit()

    # Never store/log the raw OTP.
    await _send_admin_mfa_email(
        actor.email,
        actor.name,
        otp,
    )

    return SuperAdminPasswordOtpStartResponse(
        mfa_token=mfa_token,
        message=(
            "Security OTP sent to your registered "
            "Super Admin email."
        ),
        expires_in=300,
    )


async def verify_super_admin_password_otp(
    db: AsyncSession,
    *,
    payload: SuperAdminPasswordOtpVerifyRequest,
    actor: Admin,
    request=None,
) -> PasswordChangeResponse:
    from datetime import datetime, timezone

    from fastapi import HTTPException, status

    from app.core.security import (
        hash_password,
        verify_password,
    )
    from app.features.audit.service import append_audit_log
    from app.features.auth.service import _hash_otp

    now = datetime.now(timezone.utc)

    token = (
        payload.mfa_token
        or ""
    ).strip()

    submitted_otp = (
        payload.otp
        or ""
    ).strip()

    # Password-change challenges have their own namespace.
    if not token.startswith(
        "pwdchg_"
    ):
        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role="super_admin",
            actor_name=actor.name,
            actor_identifier=str(
                actor.id
            ),
            actor_email=actor.email,
            action=(
                "SUPER_ADMIN_PASSWORD_OTP_FAILED"
            ),
            description=(
                "Invalid Super Admin password-change "
                "challenge token was submitted."
            ),
            entity_type="admin",
            entity_id=actor.id,
            source="super_admin_web",
            request=request,
            metadata={
                "reason":
                    "invalid_challenge_type",
            },
            commit=True,
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid security challenge",
        )

    otp_row = (
        await db.execute(
            select(
                AdminMFAOtp
            ).where(
                AdminMFAOtp.admin_id
                == actor.id,
                AdminMFAOtp.mfa_token
                == token,
            )
        )
    ).scalar_one_or_none()

    if otp_row is None:
        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role="super_admin",
            actor_name=actor.name,
            actor_identifier=str(
                actor.id
            ),
            actor_email=actor.email,
            action=(
                "SUPER_ADMIN_PASSWORD_OTP_FAILED"
            ),
            description=(
                "Unknown Super Admin password-change "
                "challenge was submitted."
            ),
            entity_type="admin",
            entity_id=actor.id,
            source="super_admin_web",
            request=request,
            metadata={
                "reason":
                    "challenge_not_found",
            },
            commit=True,
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired security challenge",
        )

    if otp_row.used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This security OTP has already been used",
        )

    if otp_row.expires_at <= now:
        otp_row.used = True

        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role="super_admin",
            actor_name=actor.name,
            actor_identifier=str(
                actor.id
            ),
            actor_email=actor.email,
            action=(
                "SUPER_ADMIN_PASSWORD_OTP_FAILED"
            ),
            description=(
                "Expired Super Admin password-change OTP "
                "was submitted."
            ),
            entity_type="admin",
            entity_id=actor.id,
            source="super_admin_web",
            request=request,
            metadata={
                "reason": "expired",
            },
            commit=False,
        )

        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security OTP has expired",
        )

    if otp_row.attempts >= 5:
        otp_row.used = True
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect OTP attempts",
        )

    if (
        len(submitted_otp) != 6
        or not submitted_otp.isdigit()
        or _hash_otp(
            submitted_otp
        ) != otp_row.otp_hash
    ):
        otp_row.attempts = int(
            otp_row.attempts or 0
        ) + 1

        remaining = max(
            0,
            5 - otp_row.attempts,
        )

        if otp_row.attempts >= 5:
            otp_row.used = True

        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role="super_admin",
            actor_name=actor.name,
            actor_identifier=str(
                actor.id
            ),
            actor_email=actor.email,
            action=(
                "SUPER_ADMIN_PASSWORD_OTP_FAILED"
            ),
            description=(
                "Incorrect Super Admin password-change OTP "
                "was submitted."
            ),
            entity_type="admin",
            entity_id=actor.id,
            source="super_admin_web",
            request=request,
            metadata={
                "reason": "incorrect_otp",
                "remaining_attempts":
                    remaining,
            },
            commit=False,
        )

        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Incorrect OTP. "
                f"{remaining} attempts remaining."
            ),
        )

    new_password = (
        payload.new_password
        or ""
    )

    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "New password must contain "
                "at least 8 characters"
            ),
        )

    if verify_password(
        new_password,
        actor.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "New password must be different "
                "from the current password"
            ),
        )

    old_token_version = int(
        actor.token_version or 1
    )

    actor.password_hash = hash_password(
        new_password
    )

    actor.token_version = (
        old_token_version + 1
    )

    otp_row.used = True

    # Invalidate any other outstanding password-change challenges.
    other_challenges = (
        await db.execute(
            select(
                AdminMFAOtp
            ).where(
                AdminMFAOtp.admin_id
                == actor.id,
                AdminMFAOtp.mfa_token.like(
                    "pwdchg_%"
                ),
                AdminMFAOtp.used
                == False,
            )
        )
    ).scalars().all()

    for challenge in other_challenges:
        challenge.used = True

    revoked_count = (
        await _revoke_active_sessions_for_security_change(
            db,
            target=actor,
            actor=actor,
            reason=(
                "Super Admin password changed "
                "after security OTP verification"
            ),
        )
    )

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role="super_admin",
        actor_name=actor.name,
        actor_identifier=str(
            actor.id
        ),
        actor_email=actor.email,
        action=(
            "SUPER_ADMIN_PASSWORD_OTP_VERIFIED"
        ),
        description=(
            "Super Admin password-change security OTP "
            "was successfully verified."
        ),
        entity_type="admin",
        entity_id=actor.id,
        source="super_admin_web",
        request=request,
        metadata={
            "password_change_authorized":
                True,
        },
        commit=False,
    )

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role="super_admin",
        actor_name=actor.name,
        actor_identifier=str(
            actor.id
        ),
        actor_email=actor.email,
        action=(
            "SUPER_ADMIN_PASSWORD_CHANGED"
        ),
        description=(
            "Super Admin changed their password "
            "after fresh OTP verification."
        ),
        entity_type="admin",
        entity_id=actor.id,
        source="super_admin_web",
        request=request,
        metadata={
            "revoked_sessions":
                revoked_count,
            "old_token_version":
                old_token_version,
            "new_token_version":
                actor.token_version,
        },
        commit=False,
    )

    await db.commit()

    return PasswordChangeResponse(
        message=(
            "Password changed successfully. "
            "All sessions were signed out."
        ),
        revoked_count=revoked_count,
    )


async def change_super_admin_password(
    db: AsyncSession,
    *,
    payload: SuperAdminPasswordChangeRequest,
    actor: Admin,
    request=None,
) -> PasswordChangeResponse:
    from fastapi import HTTPException, status

    from app.core.security import (
        hash_password,
        verify_password,
    )
    from app.features.audit.service import append_audit_log

    if not verify_password(
        payload.current_password,
        actor.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must contain at least 8 characters",
        )

    old_token_version = int(
        actor.token_version or 1
    )

    actor.password_hash = hash_password(
        payload.new_password
    )

    actor.token_version = (
        old_token_version + 1
    )

    revoked_count = await _revoke_active_sessions_for_security_change(
        db,
        target=actor,
        actor=actor,
        reason="Super Admin password changed",
    )

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="SUPER_ADMIN_PASSWORD_CHANGED",
        description=(
            f"{actor.name} changed their Super Admin password."
        ),
        entity_type="admin",
        entity_id=actor.id,
        source="super_admin_web",
        request=request,
        metadata={
            "revoked_sessions": revoked_count,
            "old_token_version": old_token_version,
            "new_token_version": actor.token_version,
        },
        commit=False,
    )

    await db.commit()

    return PasswordChangeResponse(
        message="Password changed successfully. Sign in again.",
        revoked_count=revoked_count,
    )


async def activate_admin_account(
    db: AsyncSession,
    *,
    target_admin_id: int,
    actor: Admin,
    request=None,
) -> AdminAccountOut:
    from fastapi import HTTPException, status

    from app.features.audit.service import append_audit_log

    result = await db.execute(
        select(Admin).where(
            Admin.id == target_admin_id
        )
    )

    target = result.scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    old_is_active = bool(target.is_active)

    if target.is_active:
        return AdminAccountOut.model_validate(target)

    target.is_active = True

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="ADMIN_ACTIVATED",
        description=(
            f"{actor.name} activated admin account "
            f"{target.email}."
        ),
        entity_type="admin",
        entity_id=target.id,
        source="super_admin_web",
        request=request,
        metadata={
            "target_admin_name": target.name,
            "target_admin_email": target.email,
            "target_admin_role": target.role,
            "old_is_active": old_is_active,
            "new_is_active": True,
        },
        commit=False,
    )

    await db.commit()
    await db.refresh(target)

    return AdminAccountOut.model_validate(target)


async def deactivate_admin_account(
    db: AsyncSession,
    *,
    target_admin_id: int,
    actor: Admin,
    request=None,
) -> AdminAccountOut:
    from fastapi import HTTPException, status

    from app.features.audit.service import append_audit_log

    result = await db.execute(
        select(Admin).where(
            Admin.id == target_admin_id
        )
    )

    target = result.scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    # Never allow a Super Admin to disable the session/account
    # they are currently using.
    if target.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot deactivate your own Super Admin account",
        )

    # At least one active Super Admin must always remain.
    if target.role == "super_admin":
        active_super_admin_count = (
            await db.execute(
                select(
                    func.count(Admin.id)
                ).where(
                    Admin.role == "super_admin",
                    Admin.is_active == True,
                )
            )
        ).scalar_one()

        if active_super_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot deactivate the last active Super Admin",
            )

    old_is_active = bool(target.is_active)
    old_token_version = int(
        target.token_version or 1
    )

    if not target.is_active:
        return AdminAccountOut.model_validate(target)

    target.is_active = False

    # Prepares this account for global token/session invalidation.
    # Enforcement will be added in the upcoming session stage.
    target.token_version = (
        old_token_version + 1
    )

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="ADMIN_DEACTIVATED",
        description=(
            f"{actor.name} deactivated admin account "
            f"{target.email}."
        ),
        entity_type="admin",
        entity_id=target.id,
        source="super_admin_web",
        request=request,
        metadata={
            "target_admin_name": target.name,
            "target_admin_email": target.email,
            "target_admin_role": target.role,
            "old_is_active": old_is_active,
            "new_is_active": False,
            "old_token_version": old_token_version,
            "new_token_version": target.token_version,
        },
        commit=False,
    )

    await db.commit()
    await db.refresh(target)

    return AdminAccountOut.model_validate(target)


# =========================================================
# ADMIN DEVICE / SESSION MANAGEMENT
# =========================================================

async def list_admin_sessions(
    db: AsyncSession,
    *,
    target_admin_id: int,
    offset: int,
    limit: int,
    current_session_id: str | None = None,
):
    from datetime import datetime, timezone

    from fastapi import HTTPException, status

    from app.features.auth.models import AdminSession
    from app.features.super_admin.schemas import (
        AdminSessionListResponse,
        AdminSessionOut,
    )

    target = (
        await db.execute(
            select(Admin).where(
                Admin.id == target_admin_id
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    total = (
        await db.execute(
            select(
                func.count(AdminSession.id)
            ).where(
                AdminSession.admin_id
                == target_admin_id
            )
        )
    ).scalar_one()

    sessions = (
        await db.execute(
            select(AdminSession)
            .where(
                AdminSession.admin_id
                == target_admin_id
            )
            .order_by(
                AdminSession.last_seen_at.desc(),
                AdminSession.created_at.desc(),
                AdminSession.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)

    items = []

    for session in sessions:
        item = AdminSessionOut.model_validate(
            session
        )

        # Show an accurate effective state even if an expired
        # session has not made another authenticated request yet.
        if (
            item.status == "active"
            and item.expires_at is not None
        ):
            expires_at = item.expires_at

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            if expires_at <= now:
                item.status = "expired"

        item.is_current = bool(
            current_session_id
            and session.session_id
            == current_session_id
        )

        items.append(item)

    return AdminSessionListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


async def revoke_admin_device_session(
    db: AsyncSession,
    *,
    session_id: str,
    actor: Admin,
    request=None,
    current_session_id: str | None = None,
):
    from fastapi import HTTPException, status

    from app.features.audit.service import (
        append_audit_log,
    )
    from app.features.auth.admin_session_service import (
        revoke_admin_session as revoke_session_record,
    )
    from app.features.auth.models import AdminSession
    from app.features.super_admin.schemas import (
        AdminSessionOut,
    )

    session = (
        await db.execute(
            select(AdminSession).where(
                AdminSession.session_id
                == session_id
            )
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin session not found",
        )

    target = (
        await db.execute(
            select(Admin).where(
                Admin.id == session.admin_id
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    was_already_revoked = (
        session.status == "revoked"
    )

    if not was_already_revoked:
        await revoke_session_record(
            db,
            session=session,
            revoked_by_admin_id=actor.id,
            reason=(
                "Revoked remotely by Super Admin"
            ),
        )

        await append_audit_log(
            db,
            actor_type="ADMIN",
            actor_id=actor.id,
            actor_role=actor.role,
            actor_name=actor.name,
            actor_identifier=str(actor.id),
            actor_email=actor.email,
            action="ADMIN_SESSION_REVOKED",
            description=(
                f"{actor.name} revoked an admin "
                f"session for {target.email}."
            ),
            entity_type="admin_session",
            entity_id=session.id,
            source="super_admin_web",
            request=request,
            metadata={
                "target_admin_id": target.id,
                "target_admin_name": target.name,
                "target_admin_email": target.email,
                "target_admin_role": target.role,
                "session_id": session.session_id,
                "device_id": session.device_id,
                "device_name": session.device_name,
                "platform": session.platform,
            },
            commit=False,
        )

        await db.commit()
        await db.refresh(session)

    item = AdminSessionOut.model_validate(
        session
    )

    item.is_current = bool(
        current_session_id
        and session.session_id
        == current_session_id
    )

    return item


async def revoke_all_admin_sessions(
    db: AsyncSession,
    *,
    target_admin_id: int,
    actor: Admin,
    request=None,
):
    from fastapi import HTTPException, status

    from app.features.audit.service import (
        append_audit_log,
    )
    from app.features.auth.admin_session_service import (
        revoke_admin_session as revoke_session_record,
    )
    from app.features.auth.models import AdminSession
    from app.features.super_admin.schemas import (
        AdminSessionsRevokedResponse,
    )

    target = (
        await db.execute(
            select(Admin).where(
                Admin.id == target_admin_id
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found",
        )

    sessions = (
        await db.execute(
            select(AdminSession).where(
                AdminSession.admin_id
                == target_admin_id,
                AdminSession.status
                == "active",
            )
        )
    ).scalars().all()

    revoked_count = 0

    for session in sessions:
        await revoke_session_record(
            db,
            session=session,
            revoked_by_admin_id=actor.id,
            reason=(
                "All sessions revoked remotely "
                "by Super Admin"
            ),
        )

        revoked_count += 1

    old_token_version = int(
        target.token_version or 1
    )

    target.token_version = (
        old_token_version + 1
    )

    await db.flush()

    await append_audit_log(
        db,
        actor_type="ADMIN",
        actor_id=actor.id,
        actor_role=actor.role,
        actor_name=actor.name,
        actor_identifier=str(actor.id),
        actor_email=actor.email,
        action="ADMIN_ALL_SESSIONS_REVOKED",
        description=(
            f"{actor.name} revoked all sessions "
            f"for {target.email}."
        ),
        entity_type="admin",
        entity_id=target.id,
        source="super_admin_web",
        request=request,
        metadata={
            "target_admin_id": target.id,
            "target_admin_name": target.name,
            "target_admin_email": target.email,
            "target_admin_role": target.role,
            "revoked_count": revoked_count,
            "old_token_version": old_token_version,
            "new_token_version": (
                target.token_version
            ),
        },
        commit=False,
    )

    await db.commit()
    await db.refresh(target)

    return AdminSessionsRevokedResponse(
        admin_id=target.id,
        revoked_count=revoked_count,
        token_version=target.token_version,
        message=(
            "All admin sessions revoked successfully"
        ),
    )
