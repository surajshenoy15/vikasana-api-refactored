"""student lifecycle foundation

Revision ID: 013_student_lifecycle_foundation
Revises: 012_batch_year_scope_indexes
Create Date: 2026-08-26

Additive lifecycle metadata for Student records.

Lifecycle:

    ACTIVE
      -> GRADUATED
      -> ARCHIVED
      -> PURGED

Important semantics:

- is_active remains unchanged and retains its existing meaning.
- PURGED does NOT mean the Student identity row was deleted.
- No existing Student/event/activity/certificate/history row is deleted.
- Existing Student rows receive lifecycle_status = ACTIVE.
- Actor identity is intentionally recorded in immutable audit_logs
  rather than duplicated into lifecycle FK columns.
- Scalable lifecycle indexes are added separately by revision 014.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "013_student_lifecycle_foundation"
down_revision: Union[str, None] = (
    "012_batch_year_scope_indexes"
)
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


LIFECYCLE_CHECK = """
lifecycle_status IN (
    'ACTIVE',
    'GRADUATED',
    'ARCHIVED',
    'PURGED'
)
"""


def upgrade() -> None:
    """
    Add lifecycle metadata without changing existing Student
    activity/account behavior.

    A constant server default is used so existing rows safely
    become ACTIVE.

    This revision intentionally contains no autocommit block.
    The lifecycle indexes are isolated in revision 014.
    """

    op.add_column(
        "students",
        sa.Column(
            "lifecycle_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text(
                "'ACTIVE'"
            ),
        ),
    )

    op.add_column(
        "students",
        sa.Column(
            "graduated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "students",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "students",
        sa.Column(
            "purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_students_lifecycle_status",
        "students",
        LIFECYCLE_CHECK,
    )


def downgrade() -> None:
    """
    Remove only the lifecycle foundation added by this revision.

    Existing Student/business records are never deleted.
    """
    op.drop_constraint(
        "ck_students_lifecycle_status",
        "students",
        type_="check",
    )

    op.drop_column(
        "students",
        "purged_at",
    )

    op.drop_column(
        "students",
        "archived_at",
    )

    op.drop_column(
        "students",
        "graduated_at",
    )

    op.drop_column(
        "students",
        "lifecycle_status",
    )
