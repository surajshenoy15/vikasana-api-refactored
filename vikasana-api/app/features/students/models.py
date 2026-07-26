from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.features.faculty.models import Faculty
    from app.features.organization.models import AcademicBatch, Department

    from app.features.activities.models import (
        ActivityFaceCheck,
        ActivityPhoto,
        ActivitySession,
        StudentActivityStats,
        StudentPointAdjustment,
    )
    from app.features.face.models import StudentFaceEmbedding


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
        # Existing uniqueness rules per college.
        UniqueConstraint(
            "college",
            "usn",
            name="uq_students_college_usn",
        ),
        UniqueConstraint(
            "college",
            "email",
            name="uq_students_college_email",
        ),
        # Current academic year remains nullable until department mode is used.
        CheckConstraint(
            "current_year IS NULL OR current_year BETWEEN 1 AND 8",
            name="ck_students_current_year",
        ),
        Index(
            "ix_students_college_branch",
            "college",
            "branch",
        ),
        Index(
            "ix_students_college_department_batch",
            "college",
            "department_id",
            "batch_id",
        ),
    )

    # --------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # --------------------------------------------------
    # BASIC DETAILS
    # --------------------------------------------------

    college: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    usn: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    branch: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    student_type: Mapped[StudentType] = mapped_column(
        SAEnum(
            StudentType,
            name="student_type_enum",
        ),
        nullable=False,
        server_default=StudentType.REGULAR.value,
    )

    # --------------------------------------------------
    # STATUS / SOFT DELETE
    # --------------------------------------------------

    # Used instead of hard delete to protect dependent records.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
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

    passout_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    admitted_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # --------------------------------------------------
    # CURRENT ORGANISATION / ACADEMIC STATE
    # --------------------------------------------------

    # Nullable so existing production students remain unaffected.
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # Current batch, such as 2026-2030.
    batch_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # Current year within the course, normally 1-4 for engineering.
    current_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    # Current faculty assignment only.
    # This does not replace created_by_faculty_id.
    assigned_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # --------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # --------------------------------------------------
    # CREATED BY FACULTY
    # --------------------------------------------------

    # Permanent record of who originally created the student.
    created_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
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
    # CURRENT ORGANISATION RELATIONSHIPS
    # --------------------------------------------------

    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[department_id],
    )

    batch: Mapped[Optional["AcademicBatch"]] = relationship(
        "AcademicBatch",
        foreign_keys=[batch_id],
    )

    assigned_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[assigned_faculty_id],
    )

    # --------------------------------------------------
    # EXISTING RELATIONSHIPS
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

    point_adjustments: Mapped[List["StudentPointAdjustment"]] = relationship(
        "StudentPointAdjustment",
        back_populates="student",
        cascade="all, delete-orphan",
    )