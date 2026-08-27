from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    RedisError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.features.auth.models import (
    Admin,
    AdminSession,
)


logger = logging.getLogger(__name__)


# =========================================================
# REDIS
# =========================================================

SESSION_KEY_PREFIX = "admin:session:"


def _session_key(
    session_id: str,
) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def _session_ttl_seconds(
    session: AdminSession,
) -> int:
    """
    Redis TTL is derived from the DB session expiry.

    Always keep at least a small positive TTL so cache writes
    do not fail around expiry boundaries.
    """

    if session.expires_at is None:
        return max(
            settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            60,
        )

    now = datetime.now(timezone.utc)

    expires_at = session.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    seconds = int(
        (
            expires_at - now
        ).total_seconds()
    )

    return max(
        seconds,
        60,
    )


async def _cache_session(
    session: AdminSession,
) -> None:
    """
    Best-effort Redis cache.

    PostgreSQL remains authoritative. Redis failure must never
    make session validation depend exclusively on stale cache.
    """

    payload = {
        "session_id": session.session_id,
        "admin_id": session.admin_id,
        "status": session.status,
        "expires_at": (
            session.expires_at.isoformat()
            if session.expires_at
            else None
        ),
    }

    try:
        redis = await get_redis()

        await redis.set(
            _session_key(
                session.session_id
            ),
            json.dumps(payload),
            ex=_session_ttl_seconds(
                session
            ),
        )

    except (
        RedisError,
        RedisConnectionError,
        OSError,
        TimeoutError,
    ) as exc:
        logger.warning(
            "Admin session Redis cache write failed: %s",
            exc,
        )


async def _get_cached_session(
    session_id: str,
) -> dict | None:
    try:
        redis = await get_redis()

        value = await redis.get(
            _session_key(
                session_id
            )
        )

        if not value:
            return None

        return json.loads(value)

    except (
        RedisError,
        RedisConnectionError,
        OSError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning(
            "Admin session Redis cache read failed: %s",
            exc,
        )

        return None


# =========================================================
# CREATE SESSION
# =========================================================

async def create_admin_session(
    db: AsyncSession,
    *,
    admin: Admin,
    device_id: str | None = None,
    device_name: str | None = None,
    device_model: str | None = None,
    device_type: str | None = None,
    platform: str | None = None,
    os_name: str | None = None,
    os_version: str | None = None,
    browser_name: str | None = None,
    browser_version: str | None = None,
    app_name: str | None = "LoRaa Admin",
    app_version: str | None = None,
    ip_address: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    user_agent: str | None = None,
) -> AdminSession:
    """
    Create one authenticated Admin/Super Admin session.

    This is called only after primary authentication + MFA
    have succeeded.
    """

    now = datetime.now(timezone.utc)

    session = AdminSession(
        session_id=str(
            uuid4()
        ),
        admin_id=admin.id,

        device_id=device_id,
        device_name=device_name,
        device_model=device_model,
        device_type=device_type,
        platform=platform,

        os_name=os_name,
        os_version=os_version,

        browser_name=browser_name,
        browser_version=browser_version,

        app_name=app_name,
        app_version=app_version,

        ip_address=ip_address,
        country=country,
        region=region,
        city=city,

        user_agent=user_agent,

        status="active",

        created_at=now,
        last_seen_at=now,

        expires_at=(
            now
            + timedelta(
                minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
            )
        ),
    )

    db.add(session)

    await db.flush()

    await _cache_session(
        session
    )

    return session


# =========================================================
# LOOKUP / VALIDATION
# =========================================================

async def get_admin_session(
    db: AsyncSession,
    *,
    session_id: str,
) -> AdminSession | None:
    result = await db.execute(
        select(AdminSession).where(
            AdminSession.session_id == session_id
        )
    )

    return result.scalar_one_or_none()


async def validate_admin_session(
    db: AsyncSession,
    *,
    session_id: str,
    admin_id: int,
) -> AdminSession | None:
    """
    Validate an Admin session.

    Redis can reject known-revoked sessions quickly.

    PostgreSQL is always checked before allowing access, so:
    - Redis outage does not restore revoked sessions.
    - Stale Redis data cannot override PostgreSQL state.
    """

    cached = await _get_cached_session(
        session_id
    )

    if (
        cached is not None
        and cached.get("status") == "revoked"
    ):
        return None

    session = await get_admin_session(
        db,
        session_id=session_id,
    )

    if session is None:
        return None

    if session.admin_id != admin_id:
        return None

    if session.status != "active":
        await _cache_session(
            session
        )
        return None

    now = datetime.now(timezone.utc)

    if session.expires_at is not None:
        expires_at = session.expires_at

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )

        if expires_at <= now:
            session.status = "expired"

            await db.flush()

            await _cache_session(
                session
            )

            return None

    # -----------------------------------------------------
    # LAST ACTIVE
    # -----------------------------------------------------
    #
    # Avoid writing to PostgreSQL on every authenticated
    # request. Refresh last_seen_at at most once every
    # five minutes for each active Admin device session.
    #
    last_seen_at = session.last_seen_at

    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(
            tzinfo=timezone.utc
        )

    if (
        now - last_seen_at
        >= timedelta(minutes=5)
    ):
        session.last_seen_at = now

        await db.flush()

    await _cache_session(
        session
    )

    return session


# =========================================================
# REVOKE
# =========================================================

async def revoke_admin_session(
    db: AsyncSession,
    *,
    session: AdminSession,
    revoked_by_admin_id: int | None = None,
    reason: str | None = None,
) -> AdminSession:
    """
    Revoke one Admin session.

    Caller owns the final DB commit so the session change can
    later be committed atomically with an immutable audit log.
    """

    if session.status == "revoked":
        return session

    session.status = "revoked"
    session.revoked_at = datetime.now(
        timezone.utc
    )
    session.revoked_by_admin_id = (
        revoked_by_admin_id
    )
    session.revoke_reason = reason

    await db.flush()

    await _cache_session(
        session
    )

    return session
