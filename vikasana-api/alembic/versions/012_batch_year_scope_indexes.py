"""batch year scope indexes

Revision ID: 012_batch_year_scope_indexes
Revises: 011_event_participant_foundation
Create Date: 2026-08-26

Additive indexes for scalable Website Faculty batch/year queries.

These indexes support:

College Coordinator:
    normalized college
    + batch
    + academic year
    + active status
    + Student.id cursor

HOD:
    normalized college
    + department
    + batch
    + academic year
    + active status
    + Student.id cursor

Important:
- No Student data is modified.
- No table or column is dropped.
- No existing index is replaced.
- Indexes are created concurrently for PostgreSQL/Aurora
  production safety.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "012_batch_year_scope_indexes"
down_revision: Union[str, None] = (
    "011_event_participant_foundation"
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


def upgrade() -> None:
    """
    Add query-shape indexes without rewriting Student rows.

    PostgreSQL requires CREATE INDEX CONCURRENTLY outside a
    transaction block, therefore Alembic's autocommit block is used.
    """

    with op.get_context().autocommit_block():
        op.create_index(
            "ix_students_scope_batch_year_active_id",
            "students",
            [
                sa.text(
                    "lower(trim(college))"
                ),
                "batch_id",
                "current_year",
                "is_active",
                "id",
            ],
            unique=False,
            postgresql_concurrently=True,
        )

        op.create_index(
            "ix_students_scope_department_batch_year_active_id",
            "students",
            [
                sa.text(
                    "lower(trim(college))"
                ),
                "department_id",
                "batch_id",
                "current_year",
                "is_active",
                "id",
            ],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    """
    Remove only the two indexes introduced by this revision.

    No Student or academic-history data is modified.
    """

    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_students_scope_department_batch_year_active_id",
            table_name="students",
            postgresql_concurrently=True,
        )

        op.drop_index(
            "ix_students_scope_batch_year_active_id",
            table_name="students",
            postgresql_concurrently=True,
        )
