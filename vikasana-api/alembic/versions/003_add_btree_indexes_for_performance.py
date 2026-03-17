"""Add B-Tree indexes for high-traffic query optimization.

These indexes support 2000+ concurrent users with fast lookups on:
- email (auth lookups)
- student_id (activity queries)
- event_id (event queries)
- activity_id (session queries)
- timestamps (sorting/filtering)

Revision ID: 003_btree_indexes
"""
from alembic import op

revision = "003_btree_indexes"
down_revision = None  # adjust to your latest revision
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Students ──
    op.create_index("ix_students_email_btree", "students", ["email"], unique=False, postgresql_using="btree")
    op.create_index("ix_students_usn_btree", "students", ["usn"], unique=False, postgresql_using="btree")
    op.create_index("ix_students_created_at", "students", ["created_at"], postgresql_using="btree")

    # ── Activity Sessions ──
    op.create_index("ix_activity_sessions_student_status", "activity_sessions", ["student_id", "status"], postgresql_using="btree")
    op.create_index("ix_activity_sessions_submitted_at", "activity_sessions", ["submitted_at"], postgresql_using="btree")
    op.create_index("ix_activity_sessions_created_at", "activity_sessions", ["created_at"], postgresql_using="btree")

    # ── Events ──
    op.create_index("ix_events_date_active", "events", ["event_date", "is_active"], postgresql_using="btree")
    op.create_index("ix_events_created_at", "events", ["created_at"], postgresql_using="btree")

    # ── Event Submissions ──
    op.create_index("ix_event_submissions_event_student", "event_submissions", ["event_id", "student_id"], postgresql_using="btree")
    op.create_index("ix_event_submissions_status", "event_submissions", ["status"], postgresql_using="btree")
    op.create_index("ix_event_submissions_submitted_at", "event_submissions", ["submitted_at"], postgresql_using="btree")

    # ── Certificates ──
    op.create_index("ix_certificates_student_event", "certificates", ["student_id", "event_id"], postgresql_using="btree")
    op.create_index("ix_certificates_issued_at", "certificates", ["issued_at"], postgresql_using="btree")

    # ── Activity Photos ──
    op.create_index("ix_activity_photos_created_at", "activity_photos", ["created_at"], postgresql_using="btree")

    # ── Faculty ──
    op.create_index("ix_faculty_college_active", "faculty", ["college", "is_active"], postgresql_using="btree")

    # ── Admins ──
    op.create_index("ix_admins_email_btree", "admins", ["email"], unique=True, postgresql_using="btree")


def downgrade() -> None:
    op.drop_index("ix_admins_email_btree")
    op.drop_index("ix_faculty_college_active")
    op.drop_index("ix_activity_photos_created_at")
    op.drop_index("ix_certificates_issued_at")
    op.drop_index("ix_certificates_student_event")
    op.drop_index("ix_event_submissions_submitted_at")
    op.drop_index("ix_event_submissions_status")
    op.drop_index("ix_event_submissions_event_student")
    op.drop_index("ix_events_created_at")
    op.drop_index("ix_events_date_active")
    op.drop_index("ix_activity_sessions_created_at")
    op.drop_index("ix_activity_sessions_submitted_at")
    op.drop_index("ix_activity_sessions_student_status")
    op.drop_index("ix_students_created_at")
    op.drop_index("ix_students_usn_btree")
    op.drop_index("ix_students_email_btree")
