"""add event photo capture mode

Revision ID: 009_event_photo_capture_mode
Revises: 008_notification_deliveries
Create Date: 2026-08-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009_event_photo_capture_mode"
down_revision: Union[str, None] = "008_notification_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "photo_capture_mode",
            sa.String(length=20),
            server_default="normal",
            nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_events_photo_capture_mode",
        "events",
        "photo_capture_mode IN ('normal', 'split_time')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_events_photo_capture_mode",
        "events",
        type_="check",
    )

    op.drop_column(
        "events",
        "photo_capture_mode",
    )
