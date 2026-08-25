"""add event linkage to activity sessions

Revision ID: 010_activity_session_link
Revises: 009_event_photo_capture_mode
"""

from alembic import op
import sqlalchemy as sa


revision = "010_activity_session_link"
down_revision = "009_event_photo_capture_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "activity_sessions",
        sa.Column(
            "event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "activity_sessions",
        sa.Column(
            "event_submission_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_activity_sessions_event_id",
        "activity_sessions",
        "events",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_activity_sessions_event_submission_id",
        "activity_sessions",
        "event_submissions",
        ["event_submission_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_activity_sessions_event_id",
        "activity_sessions",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        "ix_activity_sessions_event_submission_id",
        "activity_sessions",
        ["event_submission_id"],
        unique=False,
    )

    # Link Admin Set Points entries to one activity type.
    op.add_column(
        "student_point_adjustments",
        sa.Column(
            "activity_type_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_student_point_adjustments_activity_type_id",
        "student_point_adjustments",
        "activity_types",
        ["activity_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_student_point_adjustments_activity_type_id",
        "student_point_adjustments",
        ["activity_type_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_student_point_adjustments_activity_type_id",
        table_name="student_point_adjustments",
    )

    op.drop_constraint(
        "fk_student_point_adjustments_activity_type_id",
        "student_point_adjustments",
        type_="foreignkey",
    )

    op.drop_column(
        "student_point_adjustments",
        "activity_type_id",
    )

    op.drop_index(
        "ix_activity_sessions_event_submission_id",
        table_name="activity_sessions",
    )

    op.drop_index(
        "ix_activity_sessions_event_id",
        table_name="activity_sessions",
    )

    op.drop_constraint(
        "fk_activity_sessions_event_submission_id",
        "activity_sessions",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_activity_sessions_event_id",
        "activity_sessions",
        type_="foreignkey",
    )

    op.drop_column(
        "activity_sessions",
        "event_submission_id",
    )

    op.drop_column(
        "activity_sessions",
        "event_id",
    )
