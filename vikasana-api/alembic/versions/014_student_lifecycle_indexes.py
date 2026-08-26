"""student lifecycle indexes

Revision ID: 014_student_lifecycle_indexes
Revises: 013_student_lifecycle_foundation
Create Date: 2026-08-26

Concurrent PostgreSQL/Aurora indexes for Student lifecycle
queries.

This migration is intentionally separated from revision 013.

Why:
- 013 contains transactional lifecycle-column DDL.
- PostgreSQL CREATE INDEX CONCURRENTLY must run outside a
  transaction.
- Keeping concurrent indexes in their own revision avoids a
  partially committed lifecycle-schema migration if index
  creation fails.

No Student/business data is modified.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014_student_lifecycle_indexes"
down_revision: Union[str, None] = (
    "013_student_lifecycle_foundation"
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
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_students_scope_batch_lifecycle_id",
            "students",
            [
                sa.text(
                    "lower(trim(college))"
                ),
                "batch_id",
                "lifecycle_status",
                "id",
            ],
            unique=False,
            postgresql_concurrently=True,
        )

        op.create_index(
            "ix_students_scope_department_batch_lifecycle_id",
            "students",
            [
                sa.text(
                    "lower(trim(college))"
                ),
                "department_id",
                "batch_id",
                "lifecycle_status",
                "id",
            ],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_students_scope_department_batch_lifecycle_id",
            table_name="students",
            postgresql_concurrently=True,
        )

        op.drop_index(
            "ix_students_scope_batch_lifecycle_id",
            table_name="students",
            postgresql_concurrently=True,
        )
