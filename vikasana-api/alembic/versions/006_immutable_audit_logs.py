"""immutable audit logs foundation

Revision ID: 006_immutable_audit_logs
Revises: 005_college_hierarchy_foundation
Create Date: 2026-08-18

Central append-only audit trail for:

Admin
College Coordinator
HOD
Faculty / Mentor
Student

Important:
- Audit rows are immutable.
- UPDATE is blocked at PostgreSQL trigger level.
- DELETE is blocked at PostgreSQL trigger level.
- TRUNCATE is blocked at PostgreSQL trigger level.
- No foreign keys point back to mutable business entities.
  IDs are preserved as historical snapshots.
- Timestamps are stored timezone-aware.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "006_immutable_audit_logs"
down_revision: Union[str, None] = "005_college_hierarchy_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================
    # IMMUTABLE AUDIT LOG
    # =========================================================
    #
    # actor_id / department_id / entity_id deliberately do NOT
    # have foreign keys.
    #
    # Audit history must remain readable even if an original
    # Student, Faculty, Department, Event, College, etc. is
    # later changed or removed.
    # =========================================================

    op.create_table(
        "audit_logs",

        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),

        # -----------------------------------------------------
        # ACTOR SNAPSHOT
        # -----------------------------------------------------

        sa.Column(
            "actor_type",
            sa.String(length=40),
            nullable=False,
        ),

        sa.Column(
            "actor_id",
            sa.BigInteger(),
            nullable=True,
        ),

        sa.Column(
            "actor_role",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "actor_name",
            sa.String(length=255),
            nullable=True,
        ),

        # Student USN, Faculty identifier, Admin identifier, etc.
        sa.Column(
            "actor_identifier",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "actor_email",
            sa.String(length=255),
            nullable=True,
        ),

        # -----------------------------------------------------
        # ORGANIZATION SNAPSHOT
        # -----------------------------------------------------

        sa.Column(
            "college",
            sa.String(length=200),
            nullable=True,
        ),

        sa.Column(
            "department_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "department_name",
            sa.String(length=200),
            nullable=True,
        ),

        # -----------------------------------------------------
        # ACTION
        # -----------------------------------------------------

        sa.Column(
            "action",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),

        # -----------------------------------------------------
        # TARGET ENTITY
        # -----------------------------------------------------

        sa.Column(
            "entity_type",
            sa.String(length=80),
            nullable=True,
        ),

        sa.Column(
            "entity_id",
            sa.BigInteger(),
            nullable=True,
        ),

        # -----------------------------------------------------
        # REQUEST / DEVICE SNAPSHOT
        # -----------------------------------------------------

        # Examples:
        # admin_web
        # website_portal
        # mobile_app
        # backend
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "ip_address",
            sa.String(length=64),
            nullable=True,
        ),

        sa.Column(
            "user_agent",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "request_id",
            sa.String(length=100),
            nullable=True,
        ),

        # -----------------------------------------------------
        # EXTENSIBLE METADATA
        # -----------------------------------------------------

        sa.Column(
            "metadata_json",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=False,
            server_default=sa.text(
                "'{}'::jsonb"
            ),
        ),

        # -----------------------------------------------------
        # TIME
        # -----------------------------------------------------
        #
        # PostgreSQL timestamptz.
        # Store timezone-aware DB timestamp.
        # Frontend will display in Asia/Kolkata.
        # -----------------------------------------------------

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.CheckConstraint(
            """
            actor_type IN (
                'ADMIN',
                'COLLEGE_COORDINATOR',
                'HOD',
                'FACULTY',
                'STUDENT'
            )
            """,
            name="ck_audit_logs_actor_type",
        ),
    )


    # =========================================================
    # INDEXES
    # =========================================================

    op.create_index(
        "ix_audit_logs_created_at",
        "audit_logs",
        ["created_at"],
    )

    op.create_index(
        "ix_audit_logs_actor_type_created",
        "audit_logs",
        [
            "actor_type",
            "created_at",
        ],
    )

    op.create_index(
        "ix_audit_logs_actor_id_created",
        "audit_logs",
        [
            "actor_id",
            "created_at",
        ],
    )

    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        [
            "action",
            "created_at",
        ],
    )

    op.create_index(
        "ix_audit_logs_college_created",
        "audit_logs",
        [
            "college",
            "created_at",
        ],
    )

    op.create_index(
        "ix_audit_logs_department_created",
        "audit_logs",
        [
            "department_id",
            "created_at",
        ],
    )

    op.create_index(
        "ix_audit_logs_entity",
        "audit_logs",
        [
            "entity_type",
            "entity_id",
        ],
    )


    # =========================================================
    # DATABASE-LEVEL IMMUTABILITY
    # =========================================================
    #
    # Application code will not expose UPDATE/DELETE endpoints,
    # but PostgreSQL also blocks direct mutation.
    # =========================================================

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_logs is append-only; UPDATE, DELETE and TRUNCATE are forbidden'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )


    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE
        ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_truncate
        BEFORE TRUNCATE
        ON audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


def downgrade() -> None:
    # Audit history must never be automatically destroyed by
    # a normal Alembic downgrade.
    raise RuntimeError(
        "Downgrade blocked: audit_logs contains immutable audit history."
    )
