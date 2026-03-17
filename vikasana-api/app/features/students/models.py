from __future__ import annotations

from enum import Enum
from typing import List, Optional, TYPE_CHECKING
from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    DateTime,
    func,
    UniqueConstraint,
    Enum as SAEnum,
    Boolean,
    Index,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.features.faculty.models import Faculty
    from app.features.activities.models import ActivitySession
    from app.features.activities.models import StudentActivityStats
    from app.features.face.models import StudentFaceEmbedding
    from app.features.activities.models import ActivityFaceCheck
    from app.features.activities.models import ActivityPhoto

    # ✅ OPTIONAL (only if you add audit log table)
    from app.features.activities.models import StudentPointAdjustment


# --------------------------------------------------
# ENUM
# --------------------------------------------------

class StudentType(str, Enum):
    REGULAR = "REGULAR"
    DIPLOMA = "DIPLOMA"


# --------------------------------------------------
# MODEL
# --------------------------------------------------

class Student(Base):
    __tablename__ = "students"

    __table_args__ = (
        # ✅ FIXED: uniqueness per college
        UniqueConstraint("college", "usn", name="uq_students_college_usn"),
        UniqueConstraint("college", "email", name="uq_students_college_email"),
        Index("ix_students_college_branch", "college", "branch"),
    )

    # --------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --------------------------------------------------
    # BASIC DETAILS
    # --------------------------------------------------

    college: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    usn: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(80), nullable=False)

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    student_type: Mapped[StudentType] = mapped_column(
        SAEnum(StudentType, name="student_type_enum"),
        nullable=False,
        server_default=StudentType.REGULAR.value,
    )

    # --------------------------------------------------
    # POINTS SYSTEM
    # --------------------------------------------------

    required_total_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
    )

    total_points_earned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # --------------------------------------------------
    # FACE ENROLLMENT SYSTEM
    # --------------------------------------------------

    face_enrolled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    face_enrolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------
    # ACADEMIC YEARS
    # --------------------------------------------------

    passout_year: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted_year: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # --------------------------------------------------
    # CREATED BY (FACULTY)
    # --------------------------------------------------

    created_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("faculty.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        back_populates="students_created",
        foreign_keys=[created_by_faculty_id],
        lazy="joined",
    )

    # --------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------

    activity_sessions: Mapped[List["ActivitySession"]] = relationship(
        "ActivitySession",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    activity_stats: Mapped[List["StudentActivityStats"]] = relationship(
        "StudentActivityStats",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    face_embeddings: Mapped[List["StudentFaceEmbedding"]] = relationship(
        "StudentFaceEmbedding",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    face_checks: Mapped[List["ActivityFaceCheck"]] = relationship(
        "ActivityFaceCheck",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    activity_photos: Mapped[List["ActivityPhoto"]] = relationship(
        "ActivityPhoto",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    # ✅ OPTIONAL: if you want to store points edits history
    point_adjustments: Mapped[List["StudentPointAdjustment"]] = relationship(
        "StudentPointAdjustment",
        back_populates="student",
        cascade="all, delete-orphan",
    )