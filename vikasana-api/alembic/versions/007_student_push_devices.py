"""add student push devices

Revision ID: 007_student_push_devices
Revises: 006_immutable_audit_logs
Create Date: 2026-08-22

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_student_push_devices"
down_revision: Union[str, None] = "006_immutable_audit_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_push_devices",

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
            "expo_push_token",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "platform",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
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

        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="fk_student_push_devices_student_id_students",
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id",
            name="pk_student_push_devices",
        ),

        sa.UniqueConstraint(
            "expo_push_token",
            name="uq_student_push_devices_expo_push_token",
        ),
    )

    op.create_index(
        "ix_student_push_devices_student_id",
        "student_push_devices",
        ["student_id"],
        unique=False,
    )

    op.create_index(
        "ix_student_push_devices_is_active",
        "student_push_devices",
        ["is_active"],
        unique=False,
    )

    op.create_index(
        "ix_student_push_devices_student_active",
        "student_push_devices",
        ["student_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_push_devices_student_active",
        table_name="student_push_devices",
    )

    op.drop_index(
        "ix_student_push_devices_is_active",
        table_name="student_push_devices",
    )

    op.drop_index(
        "ix_student_push_devices_student_id",
        table_name="student_push_devices",
    )

    op.drop_table(
        "student_push_devices",
    )
