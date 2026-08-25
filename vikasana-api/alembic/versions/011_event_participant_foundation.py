"""event participant foundation

Revision ID: 011_event_participant_foundation
Revises: 010_activity_session_link
Create Date: 2026-08-26

Additive foundation for admin-managed external event participants.

Important:
- Does NOT create public signup.
- Does NOT create fake Student rows.
- Existing EventSubmission rows remain untouched.
- Existing student_id ownership continues to work.
- External participants can later own EventSubmission rows through
  event_participant_id while student_id remains NULL.
- When an external participant is later linked to a real Student,
  both event_participant_id and student_id may be present so the
  historical participation snapshot is preserved.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011_event_participant_foundation"
down_revision: Union[str, None] = "010_activity_session_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================
    # 1. PARTICIPANT CSV IMPORT BATCHES
    # =========================================================

    op.create_table(
        "event_participant_import_batches",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),

        sa.Column(
            "event_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "original_filename",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="PENDING",
        ),

        # Import summary counters.
        sa.Column(
            "total_rows",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "linked_students",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "external_created",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "duplicate_rows",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "ambiguous_rows",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "invalid_rows",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_participant_import_batches_event_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["admins.id"],
            name="fk_event_participant_import_batches_admin_id",
            ondelete="SET NULL",
        ),

        sa.CheckConstraint(
            """
            status IN (
                'PENDING',
                'PROCESSING',
                'COMPLETED',
                'FAILED'
            )
            """,
            name="ck_event_participant_import_batches_status",
        ),

        sa.CheckConstraint(
            """
            total_rows >= 0
            AND linked_students >= 0
            AND external_created >= 0
            AND duplicate_rows >= 0
            AND ambiguous_rows >= 0
            AND invalid_rows >= 0
            """,
            name="ck_event_participant_import_batches_counts",
        ),
    )

    op.create_index(
        "ix_event_participant_import_batches_event_id",
        "event_participant_import_batches",
        ["event_id"],
    )

    op.create_index(
        "ix_event_participant_import_batches_created_at",
        "event_participant_import_batches",
        ["created_at"],
    )


    # =========================================================
    # 2. EVENT PARTICIPANTS
    # =========================================================
    #
    # This is the historical participation identity.
    #
    # student_id:
    #   NULL     -> external participant
    #   NOT NULL -> linked/existing LoRaa Student
    #
    # Snapshot fields MUST NOT be rewritten when a Student's
    # current college/profile changes later.
    # =========================================================

    op.create_table(
        "event_participants",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),

        sa.Column(
            "event_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "student_id",
            sa.Integer(),
            nullable=True,
        ),

        # -----------------------------------------------------
        # HISTORICAL SNAPSHOTS
        # -----------------------------------------------------

        sa.Column(
            "name_snapshot",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "email_snapshot",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "phone_snapshot",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "usn_snapshot",
            sa.String(length=80),
            nullable=True,
        ),

        sa.Column(
            "institution_name_snapshot",
            sa.String(length=255),
            nullable=True,
        ),

        # -----------------------------------------------------
        # NORMALIZED MATCHING VALUES
        # -----------------------------------------------------
        #
        # These are matching/search helpers only.
        # Snapshot values above remain the original import values.
        # -----------------------------------------------------

        sa.Column(
            "email_normalized",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "phone_normalized",
            sa.String(length=32),
            nullable=True,
        ),

        sa.Column(
            "usn_normalized",
            sa.String(length=80),
            nullable=True,
        ),

        sa.Column(
            "institution_name_normalized",
            sa.String(length=255),
            nullable=True,
        ),

        # Stable SHA-256 fingerprint of the normalized imported
        # CSV row. Used only for idempotent Admin re-imports.
        sa.Column(
            "source_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),

        # -----------------------------------------------------
        # PARTICIPANT STATE
        # -----------------------------------------------------

        sa.Column(
            "participant_type",
            sa.String(length=30),
            nullable=False,
            server_default="EXTERNAL",
        ),

        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="ACTIVE",
        ),

        sa.Column(
            "import_batch_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        # -----------------------------------------------------
        # FOREIGN KEYS
        # -----------------------------------------------------

        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_participants_event_id",
            ondelete="RESTRICT",
        ),

        # Preserve the historical participation row if a Student
        # is ever removed/deactivated outside this feature.
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="fk_event_participants_student_id",
            ondelete="SET NULL",
        ),

        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["event_participant_import_batches.id"],
            name="fk_event_participants_import_batch_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["admins.id"],
            name="fk_event_participants_created_by_admin_id",
            ondelete="SET NULL",
        ),

        # Allows a composite FK from event_submissions so a
        # participant from Event A cannot be attached to Event B.
        sa.UniqueConstraint(
            "id",
            "event_id",
            name="uq_event_participants_id_event",
        ),

        sa.CheckConstraint(
            """
            participant_type IN (
                'EXTERNAL',
                'LORAA_STUDENT'
            )
            """,
            name="ck_event_participants_type",
        ),

        sa.CheckConstraint(
            """
            status IN (
                'ACTIVE',
                'LINKED',
                'REVIEW_REQUIRED',
                'INACTIVE'
            )
            """,
            name="ck_event_participants_status",
        ),

        # Participant ownership must agree with participant_type.
        #
        # EXTERNAL rows are historical/admin-managed identities
        # that have not yet been linked to a LoRaa Student.
        #
        # LORAA_STUDENT rows must point to exactly one Student.
        sa.CheckConstraint(
            """
            (
                participant_type = 'EXTERNAL'
                AND student_id IS NULL
            )
            OR
            (
                participant_type = 'LORAA_STUDENT'
                AND student_id IS NOT NULL
            )
            """,
            name="ck_event_participants_student_ownership",
        ),
    )

    op.create_index(
        "ix_event_participants_event_id",
        "event_participants",
        ["event_id"],
    )

    op.create_index(
        "ix_event_participants_student_id",
        "event_participants",
        ["student_id"],
    )

    op.create_index(
        "ix_event_participants_import_batch_id",
        "event_participants",
        ["import_batch_id"],
    )

    op.create_index(
        "ix_event_participants_email_normalized",
        "event_participants",
        ["email_normalized"],
    )

    op.create_index(
        "ix_event_participants_phone_normalized",
        "event_participants",
        ["phone_normalized"],
    )

    op.create_index(
        "ix_event_participants_usn_normalized",
        "event_participants",
        ["usn_normalized"],
    )

    op.create_index(
        "ix_event_participants_institution_name_normalized",
        "event_participants",
        ["institution_name_normalized"],
    )

    # Re-uploading the same normalized participant row for the same
    # Event must not create a second historical participant identity.
    op.create_index(
        "uq_event_participants_event_source_fingerprint",
        "event_participants",
        ["event_id", "source_fingerprint"],
        unique=True,
        postgresql_where=sa.text(
            "source_fingerprint IS NOT NULL"
        ),
    )

    # One real LoRaa Student can have only one participant identity
    # for the same event.
    #
    # External rows are intentionally NOT forced unique by name,
    # email, phone, or USN because ambiguous data must never be
    # automatically merged solely due to a database constraint.
    op.create_index(
        "uq_event_participants_event_student",
        "event_participants",
        ["event_id", "student_id"],
        unique=True,
        postgresql_where=sa.text(
            "student_id IS NOT NULL"
        ),
    )


    # =========================================================
    # 3. EXTERNAL PARTICIPANT LINK HISTORY
    # =========================================================
    #
    # Append-only application usage.
    #
    # Student/Admin IDs are deliberately snapshots rather than FKs
    # so a historical linking decision remains understandable even
    # if mutable business records later change.
    #
    # The existing immutable audit_logs table will additionally
    # record the Admin action when linking APIs are implemented.
    # =========================================================

    op.create_table(
        "external_participant_link_history",

        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),

        sa.Column(
            "event_participant_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "previous_student_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "student_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "matched_by",
            sa.String(length=50),
            nullable=False,
        ),

        sa.Column(
            "linked_by_admin_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="LINKED",
        ),

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

        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.ForeignKeyConstraint(
            ["event_participant_id"],
            ["event_participants.id"],
            name="fk_external_participant_link_history_participant_id",
            ondelete="RESTRICT",
        ),

        sa.CheckConstraint(
            """
            status IN (
                'LINKED',
                'RELINKED',
                'UNLINKED'
            )
            """,
            name="ck_external_participant_link_history_status",
        ),
    )

    op.create_index(
        "ix_external_participant_link_history_event_participant_id",
        "external_participant_link_history",
        ["event_participant_id"],
    )

    op.create_index(
        "ix_external_participant_link_history_student_id",
        "external_participant_link_history",
        ["student_id"],
    )

    op.create_index(
        "ix_external_participant_link_history_linked_at",
        "external_participant_link_history",
        ["linked_at"],
    )


    # =========================================================
    # DATABASE-LEVEL LINK-HISTORY IMMUTABILITY
    # =========================================================
    #
    # Linking decisions are historical audit records.
    # Application code must append new history rows rather than
    # modifying or deleting previous decisions.
    # =========================================================

    op.execute(
        """
        CREATE OR REPLACE FUNCTION
        prevent_external_participant_link_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'external_participant_link_history is append-only; UPDATE, DELETE and TRUNCATE are forbidden'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER
        trg_external_participant_link_history_no_update_delete
        BEFORE UPDATE OR DELETE
        ON external_participant_link_history
        FOR EACH ROW
        EXECUTE FUNCTION
        prevent_external_participant_link_history_mutation();
        """
    )

    op.execute(
        """
        CREATE TRIGGER
        trg_external_participant_link_history_no_truncate
        BEFORE TRUNCATE
        ON external_participant_link_history
        FOR EACH STATEMENT
        EXECUTE FUNCTION
        prevent_external_participant_link_history_mutation();
        """
    )


    # =========================================================
    # 4. LINK EVENT SUBMISSIONS TO PARTICIPANT IDENTITY
    # =========================================================

    op.add_column(
        "event_submissions",
        sa.Column(
            "event_participant_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_event_submissions_event_participant_id",
        "event_submissions",
        ["event_participant_id"],
    )

    # Participant and submission must belong to the SAME event.
    op.create_foreign_key(
        "fk_event_submissions_participant_event",
        "event_submissions",
        "event_participants",
        [
            "event_participant_id",
            "event_id",
        ],
        [
            "id",
            "event_id",
        ],
        ondelete="RESTRICT",
    )

    # A participant can have only one EventSubmission for an event.
    #
    # Existing rows have event_participant_id = NULL, therefore this
    # does not alter or conflict with existing student submissions.
    op.create_unique_constraint(
        "uq_event_submission_event_participant",
        "event_submissions",
        [
            "event_id",
            "event_participant_id",
        ],
    )

    # Existing 1,442 production submissions already satisfy this
    # because every current row has student_id populated.
    #
    # Future external submission:
    #     student_id = NULL
    #     event_participant_id = <external participant>
    #
    # After future linking:
    #     student_id = <real Student>
    #     event_participant_id remains populated
    #
    # This preserves the historical participation identity.
    op.create_check_constraint(
        "ck_event_submissions_owner_present",
        "event_submissions",
        """
        student_id IS NOT NULL
        OR event_participant_id IS NOT NULL
        """,
    )

    op.create_check_constraint(
        "ck_event_submissions_participant_requires_event",
        "event_submissions",
        """
        event_participant_id IS NULL
        OR event_id IS NOT NULL
        """,
    )


def downgrade() -> None:
    # External participant identity/link history becomes permanent
    # historical event data once this migration is applied.
    #
    # A normal Alembic downgrade must never silently destroy it.
    raise RuntimeError(
        "Downgrade blocked: event participant/link history is "
        "permanent historical data."
    )

    # The statements below are intentionally retained as migration
    # documentation but are unreachable during normal downgrade.
    op.drop_constraint(
        "ck_event_submissions_participant_requires_event",
        "event_submissions",
        type_="check",
    )

    op.drop_constraint(
        "ck_event_submissions_owner_present",
        "event_submissions",
        type_="check",
    )

    op.drop_constraint(
        "uq_event_submission_event_participant",
        "event_submissions",
        type_="unique",
    )

    op.drop_constraint(
        "fk_event_submissions_participant_event",
        "event_submissions",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_event_submissions_event_participant_id",
        table_name="event_submissions",
    )

    op.drop_column(
        "event_submissions",
        "event_participant_id",
    )

    op.drop_index(
        "ix_external_participant_link_history_linked_at",
        table_name="external_participant_link_history",
    )

    op.drop_index(
        "ix_external_participant_link_history_student_id",
        table_name="external_participant_link_history",
    )

    op.drop_index(
        "ix_external_participant_link_history_event_participant_id",
        table_name="external_participant_link_history",
    )

    op.drop_table(
        "external_participant_link_history"
    )

    op.drop_index(
        "uq_event_participants_event_student",
        table_name="event_participants",
    )

    op.drop_index(
        "uq_event_participants_event_source_fingerprint",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_institution_name_normalized",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_usn_normalized",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_phone_normalized",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_email_normalized",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_import_batch_id",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_student_id",
        table_name="event_participants",
    )

    op.drop_index(
        "ix_event_participants_event_id",
        table_name="event_participants",
    )

    op.drop_table(
        "event_participants"
    )

    op.drop_index(
        "ix_event_participant_import_batches_created_at",
        table_name="event_participant_import_batches",
    )

    op.drop_index(
        "ix_event_participant_import_batches_event_id",
        table_name="event_participant_import_batches",
    )

    op.drop_table(
        "event_participant_import_batches"
    )