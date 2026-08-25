from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


# ============================================================
# EVENT
# ============================================================


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    required_photos = Column(Integer, nullable=False, default=3)

    # Photo capture timing mode:
    # - normal: all required photos can be captured during the event
    # - split_time: event duration is divided equally across required photos
    photo_capture_mode = Column(
        String(20),
        nullable=False,
        default="normal",
        server_default="normal",
    )

    is_active = Column(Boolean, default=True)

    event_date = Column(Date, nullable=True)
    start_time = Column(Time, nullable=True)

    # IMPORTANT:
    # This is TIME in Postgres.
    end_time = Column(Time, nullable=True)

    venue_name = Column(String(255), nullable=True)
    maps_url = Column(Text, nullable=True)

    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    geo_radius_m = Column(Integer, nullable=False, default=500)

    # Used to connect participant + volunteer events
    # belonging to the same main event.
    # Example: green_circuit_2026
    exclusive_group_key = Column(
        String(120),
        nullable=True,
        index=True,
    )

    # PARTICIPANT / VOLUNTEER
    event_role = Column(
        String(30),
        nullable=False,
        default="PARTICIPANT",
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    thumbnail_url = Column(
        String,
        nullable=True,
    )

    submissions = relationship(
        "EventSubmission",
        back_populates="event",
    )

    activity_types = relationship(
        "EventActivityType",
        primaryjoin="Event.id==EventActivityType.event_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# ============================================================
# ADMIN CSV IMPORT BATCH
# ============================================================


class EventParticipantImportBatch(Base):
    """
    One Admin-managed participant CSV import for one event.

    External participant creation will only happen through
    authenticated Admin workflows built on top of this model.
    """

    __tablename__ = "event_participant_import_batches"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    event_id = Column(
        Integer,
        ForeignKey(
            "events.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    original_filename = Column(
        String(255),
        nullable=True,
    )

    status = Column(
        String(30),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    total_rows = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    linked_students = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    external_created = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    duplicate_rows = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    ambiguous_rows = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    invalid_rows = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_by_admin_id = Column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "ix_event_participant_import_batches_created_at",
            "created_at",
        ),
    )


# ============================================================
# EVENT PARTICIPANT
# ============================================================


class EventParticipant(Base):
    """
    Historical identity of one participant for one event.

    student_id == NULL
        External participant imported by Admin.

    student_id != NULL
        Existing LoRaa Student or an external participant that
        was later linked to a real Student.

    Snapshot fields represent the person's details at the time
    of the event and must not be rewritten when the Student's
    current college/profile changes later.
    """

    __tablename__ = "event_participants"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    event_id = Column(
        Integer,
        ForeignKey(
            "events.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    student_id = Column(
        Integer,
        ForeignKey(
            "students.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # --------------------------------------------------------
    # HISTORICAL SNAPSHOTS
    # --------------------------------------------------------

    name_snapshot = Column(
        String(255),
        nullable=False,
    )

    email_snapshot = Column(
        String(255),
        nullable=True,
    )

    phone_snapshot = Column(
        String(50),
        nullable=True,
    )

    usn_snapshot = Column(
        String(80),
        nullable=True,
    )

    institution_name_snapshot = Column(
        String(255),
        nullable=True,
    )

    # --------------------------------------------------------
    # NORMALIZED MATCHING VALUES
    # --------------------------------------------------------

    email_normalized = Column(
        String(255),
        nullable=True,
        index=True,
    )

    phone_normalized = Column(
        String(32),
        nullable=True,
        index=True,
    )

    usn_normalized = Column(
        String(80),
        nullable=True,
        index=True,
    )

    institution_name_normalized = Column(
        String(255),
        nullable=True,
        index=True,
    )

    # Stable normalized-row fingerprint used to make repeated
    # Admin CSV imports idempotent for the same Event.
    source_fingerprint = Column(
        String(64),
        nullable=True,
    )

    # --------------------------------------------------------
    # PARTICIPANT STATE
    # --------------------------------------------------------

    participant_type = Column(
        String(30),
        nullable=False,
        default="EXTERNAL",
        server_default="EXTERNAL",
    )

    status = Column(
        String(30),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )

    import_batch_id = Column(
        Integer,
        ForeignKey(
            "event_participant_import_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    created_by_admin_id = Column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    linked_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # Required by the composite FK from event_submissions.
        UniqueConstraint(
            "id",
            "event_id",
            name="uq_event_participants_id_event",
        ),
        CheckConstraint(
            """
            participant_type IN (
                'EXTERNAL',
                'LORAA_STUDENT'
            )
            """,
            name="ck_event_participants_type",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "uq_event_participants_event_source_fingerprint",
            "event_id",
            "source_fingerprint",
            unique=True,
            postgresql_where=(
                source_fingerprint.is_not(None)
            ),
        ),
        Index(
            "uq_event_participants_event_student",
            "event_id",
            "student_id",
            unique=True,
            postgresql_where=(
                student_id.is_not(None)
            ),
        ),
    )


# ============================================================
# EXTERNAL -> STUDENT LINK HISTORY
# ============================================================


class ExternalParticipantLinkHistory(Base):
    """
    Historical record of external participant -> Student linking.

    This table is intended to be append-only at application level.

    The existing immutable audit_logs system will separately record
    the Admin action and request metadata.
    """

    __tablename__ = "external_participant_link_history"

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    event_participant_id = Column(
        Integer,
        ForeignKey(
            "event_participants.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    # Historical snapshot IDs intentionally have no Student FK.
    previous_student_id = Column(
        Integer,
        nullable=True,
    )

    student_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    matched_by = Column(
        String(50),
        nullable=False,
    )

    linked_by_admin_id = Column(
        Integer,
        nullable=True,
    )

    status = Column(
        String(30),
        nullable=False,
        default="LINKED",
        server_default="LINKED",
    )

    metadata_json = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    linked_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
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


# ============================================================
# EVENT SUBMISSION
# ============================================================


class EventSubmission(Base):
    __tablename__ = "event_submissions"

    id = Column(
        Integer,
        primary_key=True,
    )

    event_id = Column(
        Integer,
        ForeignKey(
            "events.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    student_id = Column(
        Integer,
        ForeignKey(
            "students.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    # Historical participant identity.
    #
    # Existing student submissions:
    #     event_participant_id = NULL
    #
    # New external submission:
    #     student_id = NULL
    #     event_participant_id = participant.id
    #
    # After future linking:
    #     student_id = real Student.id
    #     event_participant_id remains populated
    event_participant_id = Column(
        Integer,
        nullable=True,
        index=True,
    )

    status = Column(
        String(30),
        default="in_progress",
    )

    description = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    submitted_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    approved_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejection_reason = Column(
        Text,
        nullable=True,
    )

    awarded_points = Column(
        Integer,
        nullable=False,
        default=0,
    )

    points_credited = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    event = relationship(
        "Event",
        back_populates="submissions",
    )

    photos = relationship(
        "EventSubmissionPhoto",
        back_populates="submission",
    )

    __table_args__ = (
        # Existing Student uniqueness rule.
        UniqueConstraint(
            "event_id",
            "student_id",
            name="uq_event_student",
        ),

        # A participant identity belongs to one event, and a
        # submission may only reference a participant from that
        # same event.
        ForeignKeyConstraint(
            [
                "event_participant_id",
                "event_id",
            ],
            [
                "event_participants.id",
                "event_participants.event_id",
            ],
            name="fk_event_submissions_participant_event",
            ondelete="RESTRICT",
        ),

        UniqueConstraint(
            "event_id",
            "event_participant_id",
            name="uq_event_submission_event_participant",
        ),

        CheckConstraint(
            """
            student_id IS NOT NULL
            OR event_participant_id IS NOT NULL
            """,
            name="ck_event_submissions_owner_present",
        ),

        CheckConstraint(
            """
            event_participant_id IS NULL
            OR event_id IS NOT NULL
            """,
            name="ck_event_submissions_participant_requires_event",
        ),
    )


# ============================================================
# EVENT SUBMISSION PHOTO
# ============================================================


class EventSubmissionPhoto(Base):
    __tablename__ = "event_submission_photos"

    id = Column(
        Integer,
        primary_key=True,
    )

    submission_id = Column(
        Integer,
        ForeignKey(
            "event_submissions.id",
            ondelete="CASCADE",
        ),
    )

    seq_no = Column(
        Integer,
        nullable=False,
    )

    image_url = Column(
        Text,
        nullable=False,
    )

    lat = Column(
        Float,
        nullable=True,
    )

    lng = Column(
        Float,
        nullable=True,
    )

    distance_m = Column(
        Float,
        nullable=True,
    )

    is_in_geofence = Column(
        Boolean,
        nullable=True,
    )

    # When row was created in DB.
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Actual photo capture time from mobile app.
    captured_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    submission = relationship(
        "EventSubmission",
        back_populates="photos",
    )

    __table_args__ = (
        UniqueConstraint(
            "submission_id",
            "seq_no",
            name="uq_submission_seq",
        ),
    )


# ============================================================
# EVENT -> ACTIVITY TYPE MAPPING
# ============================================================


class EventActivityType(Base):
    __tablename__ = "event_activity_types"

    id = Column(
        Integer,
        primary_key=True,
    )

    event_id = Column(
        Integer,
        ForeignKey(
            "events.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    activity_type_id = Column(
        Integer,
        ForeignKey(
            "activity_types.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    score_mode = Column(
        String(20),
        nullable=False,
        default="AUTO",
    )

    manual_points = Column(
        Integer,
        nullable=True,
    )

    min_required_hours = Column(
        Float,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "activity_type_id",
            name="uq_event_activity_type",
        ),
    )


# ============================================================
# EVENT PARTICIPANT / VOLUNTEER ROLE ASSIGNMENT
# ============================================================


class EventRoleAssignment(Base):
    __tablename__ = "event_role_assignments"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    student_id = Column(
        Integer,
        ForeignKey(
            "students.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    # Same key used in events.exclusive_group_key.
    # Example: green_circuit_2026
    exclusive_group_key = Column(
        String(120),
        nullable=False,
        index=True,
    )

    # VOLUNTEER / PARTICIPANT
    role_allowed = Column(
        String(30),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "exclusive_group_key",
            name="uq_student_event_group_role",
        ),
    )
