"""create faculty table

Revision ID: 002
Revises: 001
Create Date: 2026-02-20
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "faculty",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("college", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="faculty"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("activation_token_hash", sa.String(255), nullable=True),
        sa.Column("activation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_faculty_email", "faculty", ["email"], unique=True)


def downgrade():
    op.drop_index("ix_faculty_email", table_name="faculty")
    op.drop_table("faculty")
