"""create admins table

Revision ID: 001
Revises: 
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

revision  = "001"
down_revision = None
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id",            sa.Integer(),               primary_key=True, autoincrement=True),
        sa.Column("name",          sa.String(255),             nullable=False),
        sa.Column("email",         sa.String(255),             nullable=False),
        sa.Column("password_hash", sa.Text(),                  nullable=False),
        sa.Column("is_active",     sa.Boolean(),               nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admins_email", "admins", ["email"], unique=True)
    op.create_index("ix_admins_id",    "admins", ["id"],    unique=False)


def downgrade() -> None:
    op.drop_index("ix_admins_email", table_name="admins")
    op.drop_index("ix_admins_id",    table_name="admins")
    op.drop_table("admins")
