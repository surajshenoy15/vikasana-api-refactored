"""super admin and admin session foundation

Revision ID: 018_super_admin_sessions
Revises: 017_batch_purge_jobs
Create Date: 2026-08-27

Additive security foundation for LoRaa Connect admin accounts.

Adds:
- admins.role
- admins.token_version
- admin_sessions

Important:
- Existing admins remain normal "admin" accounts.
- Existing admin passwords and MFA OTP flow are unchanged.
- Existing admin accounts remain active/inactive exactly as they are.
- No Student, Faculty, HOD, College Coordinator, Event,
  Certificate, Batch or Activity data is modified.
- Session enforcement is NOT enabled by this migration alone.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "018_super_admin_sessions"
down_revision: Union[str, None] = "017_batch_purge_jobs"

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
    # =========================================================
    # ADMINS
    # =========================================================

    op.add_column(
        "admins",
        sa.Column(
            "role",
            sa.String(length=30),
            nullable=False,
            server_default="admin",
        ),
    )

    op.add_column(
        "admins",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    op.create_check_constraint(
        "ck_admins_role",
        "admins",
        "role IN ('admin', 'super_admin')",
    )

    op.create_index(
        "ix_admins_role",
        "admins",
        ["role"],
        unique=False,
    )

    # =========================================================
    # ADMIN SESSIONS
    # =========================================================

    op.create_table(
        "admin_sessions",

        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),

        # Random UUID represented as text.
        # Using String keeps the schema portable and avoids
        # tying the ORM layer directly to PostgreSQL UUID type.
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column(
            "admin_id",
            sa.Integer(),
            sa.ForeignKey(
                "admins.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),

        # -----------------------------------------------------
        # DEVICE IDENTITY
        # -----------------------------------------------------

        # LoRaa-generated installation/browser identifier.
        # Do not store hardware serial numbers.
        sa.Column(
            "device_id",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "device_name",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "device_model",
            sa.String(length=255),
            nullable=True,
        ),

        # desktop / mobile / tablet / unknown
        sa.Column(
            "device_type",
            sa.String(length=50),
            nullable=True,
        ),

        # web / ios / android
        sa.Column(
            "platform",
            sa.String(length=50),
            nullable=True,
        ),

        # -----------------------------------------------------
        # OPERATING SYSTEM / BROWSER
        # -----------------------------------------------------

        sa.Column(
            "os_name",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "os_version",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "browser_name",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "browser_version",
            sa.String(length=100),
            nullable=True,
        ),

        # -----------------------------------------------------
        # APPLICATION
        # -----------------------------------------------------

        sa.Column(
            "app_name",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "app_version",
            sa.String(length=100),
            nullable=True,
        ),

        # -----------------------------------------------------
        # NETWORK SNAPSHOT
        # -----------------------------------------------------

        sa.Column(
            "ip_address",
            sa.String(length=64),
            nullable=True,
        ),

        sa.Column(
            "country",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "region",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "city",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "user_agent",
            sa.Text(),
            nullable=True,
        ),

        # -----------------------------------------------------
        # SESSION STATE
        # -----------------------------------------------------

        # active / revoked / expired
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        # Deliberately no FK here.
        # Historical security information should remain useful
        # even if the actor admin changes later.
        sa.Column(
            "revoked_by_admin_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "revoke_reason",
            sa.Text(),
            nullable=True,
        ),

        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_admin_sessions_status",
        ),
    )

    # =========================================================
    # INDEXES
    # =========================================================

    op.create_index(
        "uq_admin_sessions_session_id",
        "admin_sessions",
        ["session_id"],
        unique=True,
    )

    op.create_index(
        "ix_admin_sessions_admin_id",
        "admin_sessions",
        ["admin_id"],
        unique=False,
    )

    op.create_index(
        "ix_admin_sessions_admin_status",
        "admin_sessions",
        [
            "admin_id",
            "status",
        ],
        unique=False,
    )

    op.create_index(
        "ix_admin_sessions_device_id",
        "admin_sessions",
        ["device_id"],
        unique=False,
    )

    op.create_index(
        "ix_admin_sessions_last_seen_at",
        "admin_sessions",
        ["last_seen_at"],
        unique=False,
    )

    op.create_index(
        "ix_admin_sessions_status_last_seen",
        "admin_sessions",
        [
            "status",
            "last_seen_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    # =========================================================
    # ADMIN SESSIONS
    # =========================================================

    op.drop_index(
        "ix_admin_sessions_status_last_seen",
        table_name="admin_sessions",
    )

    op.drop_index(
        "ix_admin_sessions_last_seen_at",
        table_name="admin_sessions",
    )

    op.drop_index(
        "ix_admin_sessions_device_id",
        table_name="admin_sessions",
    )

    op.drop_index(
        "ix_admin_sessions_admin_status",
        table_name="admin_sessions",
    )

    op.drop_index(
        "ix_admin_sessions_admin_id",
        table_name="admin_sessions",
    )

    op.drop_index(
        "uq_admin_sessions_session_id",
        table_name="admin_sessions",
    )

    op.drop_table(
        "admin_sessions",
    )

    # =========================================================
    # ADMINS
    # =========================================================

    op.drop_index(
        "ix_admins_role",
        table_name="admins",
    )

    op.drop_constraint(
        "ck_admins_role",
        "admins",
        type_="check",
    )

    op.drop_column(
        "admins",
        "token_version",
    )

    op.drop_column(
        "admins",
        "role",
    )
