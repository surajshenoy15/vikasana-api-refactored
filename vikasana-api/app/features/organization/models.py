from __future__ import annotations

from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


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

    created_by_faculty_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_by_admin_id: Mapped[int | None] = mapped_column(
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

    created_by_faculty_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_by_admin_id: Mapped[int | None] = mapped_column(
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

    from_department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    to_department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    from_batch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    to_batch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    from_year: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    to_year: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    academic_session: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    changed_by_faculty_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    changed_by_admin_id: Mapped[int | None] = mapped_column(
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

    previous_faculty_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_faculty_id: Mapped[int | None] = mapped_column(
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

    assigned_by_faculty_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_by_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
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
Index(
    "uq_academic_batches_identity_ci",
    func.lower(func.trim(AcademicBatch.college)),
    AcademicBatch.admitted_year,
    AcademicBatch.passout_year,
    AcademicBatch.course_duration_years,
    unique=True,
)