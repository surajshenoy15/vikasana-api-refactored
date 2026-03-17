from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, func, Index, Boolean
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class ActivitySessionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    FLAGGED = "FLAGGED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActivitySession(Base):
    __tablename__ = "activity_sessions"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    activity_type_id = Column(
        Integer, ForeignKey("activity_types.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )

    activity_name = Column(String(200), nullable=False)
    description = Column(String(800), nullable=True)

    session_code = Column(String(32), unique=True, nullable=False, index=True)

    started_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(
        SAEnum(ActivitySessionStatus, name="activity_session_status_enum", create_type=False),
        nullable=False,
        default=ActivitySessionStatus.DRAFT,
    )

    duration_hours = Column(Float, nullable=True)
    flag_reason = Column(String(500), nullable=True)

    # ✅ NEW (recommended): timestamp makes it auditable + avoids double-award safely
    points_awarded_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # (optional) keep points_processed only if you already shipped it and want backward compat
    # points_processed = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # -------------------------
    # RELATIONSHIPS
    # -------------------------
    student = relationship("Student", back_populates="activity_sessions")
    activity_type = relationship("ActivityType", back_populates="sessions")

    photos = relationship(
        "ActivityPhoto",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    face_checks = relationship(
        "ActivityFaceCheck",
        back_populates="session",
        cascade="all, delete-orphan",
    )


Index(
    "ix_activity_sessions_student_type_day",
    ActivitySession.student_id,
    ActivitySession.activity_type_id,
    ActivitySession.started_at,
)
# app/models/activity_type.py

import enum
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, func, Float, Text, Index
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.database import Base


class ActivityTypeStatus(str, enum.Enum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class ActivityType(Base):
    __tablename__ = "activity_types"

    id = Column(Integer, primary_key=True, index=True)

    # Identity
    name = Column(String(120), unique=True, nullable=False, index=True)
    description = Column(String(500), nullable=True)

    # Approval workflow for the type itself (optional but you already have it)
    status = Column(
        SAEnum(ActivityTypeStatus, name="activity_type_status_enum", create_type=False),
        nullable=False,
        default=ActivityTypeStatus.APPROVED,
    )

    # Scoring rule: hours_per_unit hours => points_per_unit points, capped by max_points
    hours_per_unit = Column(Float, nullable=False, default=20.0)
    points_per_unit = Column(Integer, nullable=False, default=5)
    max_points = Column(Integer, nullable=False, default=20)

    # Geofence (admin-configured)
    maps_url = Column(Text, nullable=True)
    target_lat = Column(Float, nullable=True)
    target_lng = Column(Float, nullable=True)
    radius_m = Column(Integer, nullable=False, default=500)  # meters

    # Soft enable/disable
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    sessions = relationship("ActivitySession", back_populates="activity_type")

    __table_args__ = (
        Index("ix_activity_types_active_status", "is_active", "status"),
    )
from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    DateTime,
    Float,
    Text,
    UniqueConstraint,
    func,
    Index,
    Boolean,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class ActivityPhoto(Base):
    __tablename__ = "activity_photos"

    id = Column(Integer, primary_key=True, index=True)

    session_id = Column(
        Integer,
        ForeignKey("activity_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    student_id = Column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    seq_no = Column(Integer, nullable=False, index=True)

    image_url = Column(Text, nullable=False)

    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    captured_at = Column(DateTime(timezone=True), nullable=True)

    sha256 = Column(Text, nullable=True)

    # ✅ Geofence evaluation (system-calculated)
    distance_m = Column(Float, nullable=True)
    is_in_geofence = Column(Boolean, nullable=False, default=True)
    geo_flag_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "seq_no", name="uq_activity_photos_session_seq"),
        Index("ix_activity_photos_student_session", "student_id", "session_id"),
    )

    session = relationship("ActivitySession", back_populates="photos")
    student = relationship("Student", back_populates="activity_photos")

    face_checks = relationship(
        "ActivityFaceCheck",
        back_populates="photo",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
from sqlalchemy import (
    Column,
    Integer,
    Boolean,
    ForeignKey,
    Text,
    DateTime,
    Float,
    func,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class ActivityFaceCheck(Base):
    __tablename__ = "activity_face_checks"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        Integer,
        ForeignKey("activity_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    photo_id = Column(
        Integer,
        ForeignKey("activity_photos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    matched = Column(Boolean, nullable=False, default=False)

    cosine_score = Column(Float, nullable=True)
    l2_score = Column(Float, nullable=True)
    total_faces = Column(Integer, nullable=True)

    # Required: original activity image url/key used for verification
    raw_image_url = Column(Text, nullable=False)

    # boxed/annotated output stored in MINIO_FACE_BUCKET
    processed_object = Column(Text, nullable=True)

    reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("session_id", "photo_id", name="uq_face_checks_session_photo"),
        Index("ix_face_checks_matched", "matched"),
        Index("ix_face_checks_student_session", "student_id", "session_id"),
    )

    student = relationship("Student", back_populates="face_checks", lazy="joined")
    session = relationship("ActivitySession", back_populates="face_checks", lazy="joined")
    photo = relationship("ActivityPhoto", back_populates="face_checks", lazy="joined")
from sqlalchemy import Column, Integer, DateTime, ForeignKey, Float, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base

class StudentActivityStats(Base):
    __tablename__ = "student_activity_stats"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type_id = Column(Integer, ForeignKey("activity_types.id", ondelete="RESTRICT"), nullable=False, index=True)

    total_verified_hours = Column(Float, nullable=False, default=0.0)
    points_awarded = Column(Integer, nullable=False, default=0)

    completed_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    student = relationship("Student", back_populates="activity_stats")
    activity_type = relationship("ActivityType")

    __table_args__ = (
        UniqueConstraint("student_id", "activity_type_id", name="uq_student_activity_type"),
    )
# app/models/student_activity_progress.py
from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint, func, Index
from app.core.database import Base

class StudentActivityProgress(Base):
    __tablename__ = "student_activity_progress"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type_id = Column(Integer, ForeignKey("activity_types.id", ondelete="CASCADE"), nullable=False, index=True)

    total_minutes = Column(Integer, nullable=False, default=0)
    points_awarded = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("student_id", "activity_type_id", name="uq_progress_student_activity"),
        Index("ix_progress_student_activity", "student_id", "activity_type_id"),
    )
from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlalchemy import Integer, String, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class StudentPointAdjustment(Base):
    __tablename__ = "student_point_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # actual change in total points
    delta_points: Mapped[int] = mapped_column(Integer, nullable=False)

    # total after this change
    new_total_points: Mapped[int] = mapped_column(Integer, nullable=False)

    # old field; keep for compatibility if already used
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_admin_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ✅ new UI fields
    activity_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="Manual Points")
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    activity_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="approved")
    remarks: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    student = relationship("Student", back_populates="point_adjustments")

    __table_args__ = (
        Index("ix_point_adj_student_created", "student_id", "created_at"),
    )