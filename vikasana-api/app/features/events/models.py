from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Date,
    Time,
    Float,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


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
    # This is TIME in Postgres
    end_time = Column(Time, nullable=True)

    venue_name = Column(String(255), nullable=True)
    maps_url = Column(Text, nullable=True)

    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    geo_radius_m = Column(Integer, nullable=False, default=500)

    # ✅ NEW: Used to connect participant + volunteer events of same main event
    # Example: green_circuit_2026
    exclusive_group_key = Column(String(120), nullable=True, index=True)

    # ✅ NEW: PARTICIPANT / VOLUNTEER
    event_role = Column(String(30), nullable=False, default="PARTICIPANT")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    thumbnail_url = Column(String, nullable=True)

    submissions = relationship("EventSubmission", back_populates="event")

    activity_types = relationship(
        "EventActivityType",
        primaryjoin="Event.id==EventActivityType.event_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EventSubmission(Base):
    __tablename__ = "event_submissions"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"))
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"))

    status = Column(String(30), default="in_progress")
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    awarded_points = Column(Integer, nullable=False, default=0)
    points_credited = Column(Boolean, nullable=False, default=False)

    event = relationship("Event", back_populates="submissions")
    photos = relationship("EventSubmissionPhoto", back_populates="submission")

    __table_args__ = (UniqueConstraint("event_id", "student_id", name="uq_event_student"),)


class EventSubmissionPhoto(Base):
    __tablename__ = "event_submission_photos"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("event_submissions.id", ondelete="CASCADE"))

    seq_no = Column(Integer, nullable=False)
    image_url = Column(Text, nullable=False)

    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    distance_m = Column(Float, nullable=True)
    is_in_geofence = Column(Boolean, nullable=True)

    # when row was created in DB
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # actual photo capture time from mobile app
    captured_at = Column(DateTime(timezone=True), nullable=True)

    submission = relationship("EventSubmission", back_populates="photos")

    __table_args__ = (
        UniqueConstraint("submission_id", "seq_no", name="uq_submission_seq"),
    )


class EventActivityType(Base):
    __tablename__ = "event_activity_types"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type_id = Column(Integer, ForeignKey("activity_types.id", ondelete="RESTRICT"), nullable=False, index=True)

    # NEW
    score_mode = Column(String(20), nullable=False, default="AUTO")
    manual_points = Column(Integer, nullable=True)
    min_required_hours = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "activity_type_id", name="uq_event_activity_type"),
    )


# ✅ NEW: stores who is allowed as volunteer/participant for one main event group
class EventRoleAssignment(Base):
    __tablename__ = "event_role_assignments"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Same key used in events.exclusive_group_key
    # Example: green_circuit_2026
    exclusive_group_key = Column(String(120), nullable=False, index=True)

    # VOLUNTEER / PARTICIPANT
    role_allowed = Column(String(30), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "exclusive_group_key",
            name="uq_student_event_group_role",
        ),
    )