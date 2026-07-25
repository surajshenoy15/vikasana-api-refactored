"""Recover missing production Alembic revision.

Revision ID: a00fa6471be0
Revises: 002

The production database is already stamped at this revision, but the
original migration file was lost.

This recovery migration intentionally performs no database changes.
It exists only to restore the Alembic revision chain.

Important:
Do not use this revision to downgrade production below a00fa6471be0,
because the operations from the original missing migration are unknown.
"""

revision = "a00fa6471be0"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No operation.

    Production is already recorded at this revision.
    """
    pass


def downgrade() -> None:
    """No operation.

    Downgrading across this recovered revision is unsupported.
    """
    pass
