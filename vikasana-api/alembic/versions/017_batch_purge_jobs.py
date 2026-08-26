"""batch purge job foundation

Revision ID: 017_batch_purge_jobs
Revises: 016_batch_export_active_unique
Create Date: 2026-08-26

Additive durable metadata foundation for ARCHIVED -> PURGED jobs.

Important:
- Does NOT purge Students.
- Does NOT delete PostgreSQL business rows.
- Does NOT delete object-storage objects.
- Does NOT modify lifecycle_status.
- Does NOT modify certificates, event submissions, academic
  history, immutable audit logs or verified export evidence.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "017_batch_purge_jobs"
down_revision: Union[str, None] = (
    "016_batch_export_active_unique"
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
        "batch_purge_jobs",

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
            "export_job_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "requested_by_faculty_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "reason",
            sa.String(length=500),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
        ),

        # ------------------------------------------------
        # Student accounting
        # ------------------------------------------------

        sa.Column(
            "students_targeted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "students_purged",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        # ------------------------------------------------
        # DB deletion accounting
        # ------------------------------------------------

        sa.Column(
            "activity_sessions_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_photos_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_face_checks_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "event_submission_photos_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "face_embeddings_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "face_enrollment_images_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "push_devices_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "notification_deliveries_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "event_role_assignments_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "faculty_assignments_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_progress_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "activity_stats_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "point_adjustments_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        # ------------------------------------------------
        # Storage accounting
        # ------------------------------------------------

        sa.Column(
            "storage_objects_targeted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "storage_objects_deleted",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "ignored_storage_metadata",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "unsafe_storage_references",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),

        # ------------------------------------------------
        # Durable checkpoints
        # ------------------------------------------------

        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "storage_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "database_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "completed_at",
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

        # ------------------------------------------------
        # Foreign keys
        # ------------------------------------------------

        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["academic_batches.id"],
            name="fk_batch_purge_jobs_batch_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_batch_purge_jobs_department_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["export_job_id"],
            ["batch_export_jobs.id"],
            name="fk_batch_purge_jobs_export_job_id",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["requested_by_faculty_id"],
            ["faculty.id"],
            name="fk_batch_purge_jobs_requested_by_faculty_id",
            ondelete="SET NULL",
        ),

        # ------------------------------------------------
        # Safety constraints
        # ------------------------------------------------

        sa.CheckConstraint(
            """
            scope_type IN (
                'college',
                'department'
            )
            """,
            name="ck_batch_purge_jobs_scope_type",
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
            name="ck_batch_purge_jobs_scope_department",
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
            name="ck_batch_purge_jobs_status",
        ),

        sa.CheckConstraint(
            """
            students_targeted >= 0
            AND students_purged >= 0

            AND activity_sessions_deleted >= 0
            AND activity_photos_deleted >= 0
            AND activity_face_checks_deleted >= 0
            AND event_submission_photos_deleted >= 0

            AND face_embeddings_deleted >= 0
            AND face_enrollment_images_deleted >= 0

            AND push_devices_deleted >= 0
            AND notification_deliveries_deleted >= 0

            AND event_role_assignments_deleted >= 0
            AND faculty_assignments_deleted >= 0

            AND activity_progress_deleted >= 0
            AND activity_stats_deleted >= 0
            AND point_adjustments_deleted >= 0

            AND storage_objects_targeted >= 0
            AND storage_objects_deleted >= 0
            AND ignored_storage_metadata >= 0
            AND unsafe_storage_references >= 0
            """,
            name="ck_batch_purge_jobs_counts",
        ),

        sa.CheckConstraint(
            """
            students_purged
            <= students_targeted
            """,
            name="ck_batch_purge_jobs_student_counts",
        ),

        sa.CheckConstraint(
            """
            storage_objects_deleted
            <= storage_objects_targeted
            """,
            name="ck_batch_purge_jobs_storage_counts",
        ),

        sa.CheckConstraint(
            """
            status <> 'COMPLETED'
            OR (
                started_at IS NOT NULL
                AND storage_completed_at IS NOT NULL
                AND database_completed_at IS NOT NULL
                AND completed_at IS NOT NULL
                AND unsafe_storage_references = 0
                AND students_purged = students_targeted
            )
            """,
            name="ck_batch_purge_jobs_completed_state",
        ),
    )

    op.create_index(
        "ix_batch_purge_jobs_batch_scope_status_created",
        "batch_purge_jobs",
        [
            "batch_id",
            "scope_type",
            "department_id",
            "status",
            "created_at",
        ],
    )

    op.create_index(
        "ix_batch_purge_jobs_export_job",
        "batch_purge_jobs",
        [
            "export_job_id",
            "created_at",
        ],
    )

    # Database-level concurrency guard.
    #
    # Exact active scope:
    # batch + normalized college + scope + department.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_batch_purge_jobs_active_scope
        ON batch_purge_jobs (
            batch_id,
            lower(btrim(college)),
            scope_type,
            COALESCE(department_id, 0)
        )
        WHERE status IN ('PENDING', 'PROCESSING')
        """
    )


def downgrade() -> None:
    """
    Remove only durable purge-job metadata.

    No Student, activity, event, certificate, history, export
    object or audit record is modified.
    """

    op.execute(
        """
        DROP INDEX IF EXISTS
            uq_batch_purge_jobs_active_scope
        """
    )

    op.drop_index(
        "ix_batch_purge_jobs_export_job",
        table_name="batch_purge_jobs",
    )

    op.drop_index(
        "ix_batch_purge_jobs_batch_scope_status_created",
        table_name="batch_purge_jobs",
    )

    op.drop_table(
        "batch_purge_jobs"
    )
