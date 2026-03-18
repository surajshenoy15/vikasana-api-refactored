"""Add B-Tree indexes for currently existing high-traffic tables.

Revision ID: 003_btree_indexes
Revises: 002
"""

from alembic import op

revision = "003_btree_indexes"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Faculty ──
    op.create_index(
        "ix_faculty_college_active",
        "faculty",
        ["college", "is_active"],
        postgresql_using="btree",
    )

    # ── Admins ──
    op.create_index(
        "ix_admins_email_btree",
        "admins",
        ["email"],
        unique=True,
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("ix_admins_email_btree", table_name="admins")
    op.drop_index("ix_faculty_college_active", table_name="faculty")