"""batch export job foundation

Revision ID: 015_batch_export_jobs
Revises: 014_student_lifecycle_indexes
Create Date: 2026-08-26

Additive durable metadata foundation for final academic-batch exports.

Important:
- Does NOT generate ZIP files.
- Does NOT upload anything to object storage.
- Does NOT archive or purge Students.
- Does NOT delete existing business data.
- verified_at will later be the gate for GRADUATED -> ARCHIVED.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "015_batch_export_jobs"
down_revision: Union[str, None] = (
    "014_student_lifecycle_indexes"
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
    op.create_table(
        "batch_export_jobs",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),

        sa.Column(
            "batch_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "college",
            sa.String(length=200),
            nullable=False,
        ),

        sa.Column(
            "department_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "scope_type",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "requested_by_faculty_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
        ),

        sa.Column(
            "storage_provider",
            sa.String(length=20),
            nullable=True,
        ),

        sa.Column(
            "bucket",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "object_key",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "sha256",
            sa.String(length=64),
            nullable=True,
        ),

        sa.Column(
            "file_size_bytes",
            sa.BigInteger(),
            nullable=True,
        ),

        sa.Column(
            "students_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "academic_history_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_records_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_points_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "event_participation_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "certificates_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "failure_reason",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["academic_batches.id"],
            name="fk_batch_export_jobs_batch_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_batch_export_jobs_department_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["requested_by_faculty_id"],
            ["faculty.id"],
            name="fk_batch_export_jobs_requested_by_faculty_id",
            ondelete="SET NULL",
        ),

        sa.CheckConstraint(
            """
            scope_type IN (
                'college',
                'department'
            )
            """,
            name="ck_batch_export_jobs_scope_type",
        ),

        sa.CheckConstraint(
            """
            (
                scope_type = 'college'
                AND department_id IS NULL
            )
            OR
            (
                scope_type = 'department'
                AND department_id IS NOT NULL
            )
            """,
            name="ck_batch_export_jobs_scope_department",
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
            name="ck_batch_export_jobs_status",
        ),

        sa.CheckConstraint(
            """
            students_rows >= 0
            AND academic_history_rows >= 0
            AND activity_records_rows >= 0
            AND activity_points_rows >= 0
            AND event_participation_rows >= 0
            AND certificates_rows >= 0
            """,
            name="ck_batch_export_jobs_row_counts",
        ),

        sa.CheckConstraint(
            """
            file_size_bytes IS NULL
            OR file_size_bytes >= 0
            """,
            name="ck_batch_export_jobs_file_size",
        ),

        sa.CheckConstraint(
            """
            status <> 'COMPLETED'
            OR (
                storage_provider IS NOT NULL
                AND bucket IS NOT NULL
                AND object_key IS NOT NULL
                AND sha256 IS NOT NULL
                AND file_size_bytes IS NOT NULL
                AND completed_at IS NOT NULL
            )
            """,
            name="ck_batch_export_jobs_completed_metadata",
        ),

        sa.CheckConstraint(
            """
            verified_at IS NULL
            OR status = 'COMPLETED'
            """,
            name="ck_batch_export_jobs_verified_status",
        ),
    )

    op.create_index(
        "ix_batch_export_jobs_batch_scope_status_created",
        "batch_export_jobs",
        [
            "batch_id",
            "scope_type",
            "department_id",
            "status",
            "created_at",
        ],
    )

    op.create_index(
        "ix_batch_export_jobs_verified_gate",
        "batch_export_jobs",
        [
            "batch_id",
            "scope_type",
            "department_id",
            "verified_at",
        ],
    )


def downgrade() -> None:
    """
    Remove only the export-job foundation.

    Student, activity, event, history and certificate records are
    never modified by this downgrade.
    """

    op.drop_index(
        "ix_batch_export_jobs_verified_gate",
        table_name="batch_export_jobs",
    )

    op.drop_index(
        "ix_batch_export_jobs_batch_scope_status_created",
        table_name="batch_export_jobs",
    )

    op.drop_table(
        "batch_export_jobs"
    )
