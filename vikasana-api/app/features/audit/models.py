from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    """
    Immutable append-only application audit entry.

    IMPORTANT:
    - No ORM relationships are intentionally defined.
    - Historical actor/entity IDs remain valid snapshots even if the
      original business record later changes or is removed.
    - PostgreSQL triggers created by migration 006 block UPDATE,
      DELETE and TRUNCATE.
    """

    __tablename__ = "audit_logs"

    __table_args__ = (
        CheckConstraint(
            """
            actor_type IN (
                'ADMIN',
                'COLLEGE_COORDINATOR',
                'HOD',
                'FACULTY',
                'STUDENT'
            )
            """,
            name="ck_audit_logs_actor_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    # =========================================================
    # ACTOR SNAPSHOT
    # =========================================================

    actor_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    actor_role: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    actor_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    actor_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    actor_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # =========================================================
    # ORGANIZATION SNAPSHOT
    # =========================================================

    college: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    department_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    department_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # =========================================================
    # ACTION
    # =========================================================

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # TARGET ENTITY
    # =========================================================

    entity_type: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )

    entity_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    # =========================================================
    # REQUEST / DEVICE SNAPSHOT
    # =========================================================

    source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # =========================================================
    # EXTENSIBLE METADATA
    # =========================================================

    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # =========================================================
    # TIME
    # =========================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
