"""add student notification deliveries

Revision ID: 008_notification_deliveries
Revises: 007_student_push_devices
Create Date: 2026-08-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_notification_deliveries"
down_revision: Union[str, None] = "007_student_push_devices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_notification_deliveries",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "student_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "event_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "notification_type",
            sa.String(length=80),
            nullable=False,
        ),

        sa.Column(
            "dedupe_key",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            server_default="PENDING",
            nullable=False,
        ),

        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="fk_student_notification_deliveries_student_id_students",
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_student_notification_deliveries_event_id_events",
            ondelete="SET NULL",
        ),

        sa.PrimaryKeyConstraint(
            "id",
            name="pk_student_notification_deliveries",
        ),

        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_student_notification_deliveries_dedupe_key",
        ),
    )

    op.create_index(
        "ix_student_notification_deliveries_student_id",
        "student_notification_deliveries",
        ["student_id"],
        unique=False,
    )

    op.create_index(
        "ix_student_notification_deliveries_event_id",
        "student_notification_deliveries",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        "ix_student_notification_deliveries_type",
        "student_notification_deliveries",
        ["notification_type"],
        unique=False,
    )

    op.create_index(
        "ix_student_notification_deliveries_status",
        "student_notification_deliveries",
        ["status"],
        unique=False,
    )

    op.create_index(
        "ix_student_notification_deliveries_scheduled_for",
        "student_notification_deliveries",
        ["scheduled_for"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_notification_deliveries_scheduled_for",
        table_name="student_notification_deliveries",
    )

    op.drop_index(
        "ix_student_notification_deliveries_status",
        table_name="student_notification_deliveries",
    )

    op.drop_index(
        "ix_student_notification_deliveries_type",
        table_name="student_notification_deliveries",
    )

    op.drop_index(
        "ix_student_notification_deliveries_event_id",
        table_name="student_notification_deliveries",
    )

    op.drop_index(
        "ix_student_notification_deliveries_student_id",
        table_name="student_notification_deliveries",
    )

    op.drop_table(
        "student_notification_deliveries",
    )
