from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int

    actor_type: str
    actor_id: int | None = None
    actor_role: str | None = None
    actor_name: str | None = None
    actor_identifier: str | None = None
    actor_email: str | None = None

    college: str | None = None
    department_id: int | None = None
    department_name: str | None = None

    action: str
    description: str | None = None

    entity_type: str | None = None
    entity_id: int | None = None

    source: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
    )

    created_at: datetime


class AuditLogListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[AuditLogResponse]
