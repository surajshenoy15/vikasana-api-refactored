"""Prevent duplicate active batch export jobs.

Revision ID: 016_batch_export_active_unique
Revises: 015_batch_export_jobs
"""

from alembic import op


revision = "016_batch_export_active_unique"
down_revision = "015_batch_export_jobs"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_batch_export_jobs_active_scope"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX {INDEX_NAME}
        ON batch_export_jobs (
            batch_id,
            lower(btrim(college)),
            scope_type,
            COALESCE(department_id, 0)
        )
        WHERE status IN ('PENDING', 'PROCESSING')
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP INDEX IF EXISTS {INDEX_NAME}
        """
    )
