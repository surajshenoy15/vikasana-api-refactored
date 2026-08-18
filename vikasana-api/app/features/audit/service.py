from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.audit.models import AuditLog


VALID_ACTOR_TYPES = {
    "ADMIN",
    "COLLEGE_COORDINATOR",
    "HOD",
    "FACULTY",
    "STUDENT",
}


def normalize_actor_type(
    value: str,
) -> str:
    actor_type = (
        str(value or "")
        .strip()
        .upper()
    )

    if actor_type not in VALID_ACTOR_TYPES:
        raise ValueError(
            f"Unsupported audit actor type: {actor_type!r}"
        )

    return actor_type


def get_request_ip(
    request: Request | None,
) -> str | None:
    """
    Best-effort client IP capture.

    LoRaa Connect runs behind proxies, so use the first
    X-Forwarded-For value when available. Otherwise use
    request.client.host.
    """

    if request is None:
        return None

    forwarded_for = (
        request.headers.get(
            "x-forwarded-for"
        )
        or ""
    ).strip()

    if forwarded_for:
        first_ip = (
            forwarded_for
            .split(",", 1)[0]
            .strip()
        )

        if first_ip:
            return first_ip[:64]

    if request.client:
        host = (
            request.client.host
            or ""
        ).strip()

        if host:
            return host[:64]

    return None


def get_user_agent(
    request: Request | None,
) -> str | None:
    if request is None:
        return None

    value = (
        request.headers.get(
            "user-agent"
        )
        or ""
    ).strip()

    return value or None


def get_request_id(
    request: Request | None,
) -> str | None:
    if request is None:
        return None

    for header in (
        "x-request-id",
        "x-correlation-id",
        "cf-ray",
    ):
        value = (
            request.headers.get(header)
            or ""
        ).strip()

        if value:
            return value[:100]

    return None


async def append_audit_log(
    db: AsyncSession,
    *,
    actor_type: str,
    action: str,

    actor_id: int | None = None,
    actor_role: str | None = None,
    actor_name: str | None = None,
    actor_identifier: str | None = None,
    actor_email: str | None = None,

    college: str | None = None,
    department_id: int | None = None,
    department_name: str | None = None,

    description: str | None = None,

    entity_type: str | None = None,
    entity_id: int | None = None,

    source: str | None = None,

    request: Request | None = None,

    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,

    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """
    Append one immutable audit entry.

    This helper performs INSERT only.

    It deliberately commits the audit entry because callers will invoke
    it only after the business/authentication action has succeeded.

    There are intentionally no audit update/delete helpers.
    """

    normalized_actor_type = normalize_actor_type(
        actor_type
    )

    normalized_action = (
        str(action or "")
        .strip()
        .upper()
    )

    if not normalized_action:
        raise ValueError(
            "Audit action is required"
        )

    row = AuditLog(
        actor_type=normalized_actor_type,
        actor_id=actor_id,
        actor_role=(
            str(actor_role).strip()
            if actor_role is not None
            else None
        ),
        actor_name=(
            str(actor_name).strip()
            if actor_name is not None
            else None
        ),
        actor_identifier=(
            str(actor_identifier).strip()
            if actor_identifier is not None
            else None
        ),
        actor_email=(
            str(actor_email).strip().lower()
            if actor_email is not None
            else None
        ),
        college=(
            str(college).strip()
            if college is not None
            else None
        ),
        department_id=department_id,
        department_name=(
            str(department_name).strip()
            if department_name is not None
            else None
        ),
        action=normalized_action,
        description=description,
        entity_type=(
            str(entity_type).strip()
            if entity_type is not None
            else None
        ),
        entity_id=entity_id,
        source=(
            str(source).strip()
            if source is not None
            else None
        ),
        ip_address=(
            ip_address
            or get_request_ip(request)
        ),
        user_agent=(
            user_agent
            or get_user_agent(request)
        ),
        request_id=(
            request_id
            or get_request_id(request)
        ),
        metadata_json=dict(
            metadata or {}
        ),
    )

    db.add(row)

    await db.commit()
    await db.refresh(row)

    return row
