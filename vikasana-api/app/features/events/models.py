# app/models/events.py
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
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    required_photos = Column(Integer, nullable=False, default=3)
    is_active = Column(Boolean, default=True)

    event_date = Column(Date, nullable=True)
    start_time = Column(Time, nullable=True)

    # IMPORTANT:
    # This is TIMESTAMP WITHOUT TIME ZONE in Postgres by default.
    # So we store NAIVE datetime in controllers.
    end_time = Column(Time, nullable=True)

    # ✅ Location fields (existing)
    venue_name = Column(String(255), nullable=True)
    maps_url = Column(Text, nullable=True)

    # ✅ NEW: Event geofence target (admin sets this)
    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)

    # ✅ NEW: Radius in meters (default 500)
    geo_radius_m = Column(Integer, nullable=False, default=500)

    # timezone aware UTC
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    thumbnail_url = Column(String, nullable=True)

    submissions = relationship("EventSubmission", back_populates="event")

    # ✅ mapping rows -> event_activity_types table
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

    status = Column(String(30), default="in_progress")  # in_progress/submitted/approved/rejected/expired
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    # ✅ ADD THESE
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

    # ✅ NEW: store GPS (student upload time)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    # ✅ NEW: optional computed fields (helps admin/debug)
    distance_m = Column(Float, nullable=True)
    is_in_geofence = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    submission = relationship("EventSubmission", back_populates="photos")

    __table_args__ = (UniqueConstraint("submission_id", "seq_no", name="uq_submission_seq"),)
from sqlalchemy import Column, Integer, ForeignKey, UniqueConstraint
from app.core.database import Base


class EventActivityType(Base):
    __tablename__ = "event_activity_types"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type_id = Column(Integer, ForeignKey("activity_types.id", ondelete="RESTRICT"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("event_id", "activity_type_id", name="uq_event_activity_type"),
    )