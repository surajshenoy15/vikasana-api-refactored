"""Add B-Tree indexes for currently existing tables.

Revision ID: 003_btree_indexes
Revises: 002
Create Date: 2026-02-20
"""
from alembic import op

revision = "003_btree_indexes"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_faculty_college_active",
        "faculty",
        ["college", "is_active"],
        postgresql_using="btree",
    )
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