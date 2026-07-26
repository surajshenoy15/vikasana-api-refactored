from __future__ import annotations

from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.features.auth.models import Admin
    from app.features.faculty.models import Faculty
    from app.features.students.models import Student


# --------------------------------------------------
# COLLEGE ORGANISATION SETTINGS
# --------------------------------------------------


class CollegeOrganizationSetting(Base):
    __tablename__ = "college_organization_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    department_architecture_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    academic_year_start_month: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=7,
        server_default=text("7"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "academic_year_start_month BETWEEN 1 AND 12",
            name="ck_college_org_academic_year_start_month",
        ),
        Index(
            "ix_college_organization_settings_college",
            "college",
        ),
    )


Index(
    "uq_college_organization_settings_college_ci",
    func.lower(func.trim(CollegeOrganizationSetting.college)),
    unique=True,
)


# --------------------------------------------------
# DEPARTMENT
# --------------------------------------------------


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    faculty_members: Mapped[List["Faculty"]] = relationship(
        "Faculty",
        back_populates="department",
        foreign_keys="Faculty.department_id",
    )

    __table_args__ = (
        Index(
            "ix_departments_college",
            "college",
        ),
        Index(
            "ix_departments_college_active",
            "college",
            "is_active",
        ),
    )


Index(
    "uq_departments_college_name_ci",
    func.lower(func.trim(Department.college)),
    func.lower(func.trim(Department.name)),
    unique=True,
)


Index(
    "uq_departments_college_code_ci",
    func.lower(func.trim(Department.college)),
    func.lower(func.trim(Department.code)),
    unique=True,
)


# --------------------------------------------------
# ACADEMIC BATCH
# --------------------------------------------------


class AcademicBatch(Base):
    __tablename__ = "academic_batches"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    admitted_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    passout_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    course_duration_years: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=4,
        server_default=text("4"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "passout_year > admitted_year",
            name="ck_academic_batches_year_order",
        ),
        CheckConstraint(
            "course_duration_years BETWEEN 1 AND 8",
            name="ck_academic_batches_duration",
        ),
        Index(
            "ix_academic_batches_college",
            "college",
        ),
        Index(
            "ix_academic_batches_college_active",
            "college",
            "is_active",
        ),
    )


Index(
    "uq_academic_batches_identity_ci",
    func.lower(func.trim(AcademicBatch.college)),
    AcademicBatch.admitted_year,
    AcademicBatch.passout_year,
    AcademicBatch.course_duration_years,
    unique=True,
)


# --------------------------------------------------
# STUDENT ACADEMIC HISTORY
# --------------------------------------------------


class StudentAcademicHistory(Base):
    __tablename__ = "student_academic_history"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "students.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    from_department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    to_department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    from_batch_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    to_batch_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    from_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    to_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    academic_session: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    changed_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    changed_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    student: Mapped["Student"] = relationship(
        "Student",
        back_populates="academic_history",
        foreign_keys=[student_id],
    )

    from_department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[from_department_id],
    )

    to_department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[to_department_id],
    )

    from_batch: Mapped[Optional["AcademicBatch"]] = relationship(
        "AcademicBatch",
        foreign_keys=[from_batch_id],
    )

    to_batch: Mapped[Optional["AcademicBatch"]] = relationship(
        "AcademicBatch",
        foreign_keys=[to_batch_id],
    )

    changed_by_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[changed_by_faculty_id],
    )

    changed_by_admin: Mapped[Optional["Admin"]] = relationship(
        "Admin",
        foreign_keys=[changed_by_admin_id],
    )

    __table_args__ = (
        CheckConstraint(
            "from_year IS NULL OR from_year BETWEEN 1 AND 8",
            name="ck_student_history_from_year",
        ),
        CheckConstraint(
            "to_year IS NULL OR to_year BETWEEN 1 AND 8",
            name="ck_student_history_to_year",
        ),
        Index(
            "ix_student_academic_history_student_created",
            "student_id",
            "created_at",
        ),
        Index(
            "ix_student_academic_history_changed_by_faculty",
            "changed_by_faculty_id",
        ),
    )


# --------------------------------------------------
# STUDENT FACULTY ASSIGNMENT HISTORY
# --------------------------------------------------


class StudentFacultyAssignment(Base):
    __tablename__ = "student_faculty_assignments"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "students.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    previous_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    assigned_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    note: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    student: Mapped["Student"] = relationship(
        "Student",
        back_populates="faculty_assignment_history",
        foreign_keys=[student_id],
    )

    previous_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[previous_faculty_id],
    )

    assigned_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[assigned_faculty_id],
    )

    assigned_by_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[assigned_by_faculty_id],
    )

    assigned_by_admin: Mapped[Optional["Admin"]] = relationship(
        "Admin",
        foreign_keys=[assigned_by_admin_id],
    )

    __table_args__ = (
        Index(
            "ix_student_faculty_assignments_student_created",
            "student_id",
            "created_at",
        ),
        Index(
            "ix_student_faculty_assignments_assigned_faculty",
            "assigned_faculty_id",
        ),
    )