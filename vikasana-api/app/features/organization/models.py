from __future__ import annotations

from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
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
# COLLEGE MASTER
# --------------------------------------------------


class College(Base):
    __tablename__ = "colleges"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    code: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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

    aliases: Mapped[List["CollegeAlias"]] = relationship(
        "CollegeAlias",
        back_populates="college",
    )

    __table_args__ = (
        Index(
            "ix_colleges_name",
            "name",
        ),
    )


Index(
    "uq_colleges_name_ci",
    func.lower(func.trim(College.name)),
    unique=True,
)

Index(
    "uq_colleges_code_ci",
    func.lower(func.trim(College.code)),
    unique=True,
    postgresql_where=text(
        "code IS NOT NULL AND TRIM(code) <> ''"
    ),
)


# --------------------------------------------------
# COLLEGE ALIASES
# --------------------------------------------------


class CollegeAlias(Base):
    __tablename__ = "college_aliases"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    college_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "colleges.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    alias: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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

    college: Mapped["College"] = relationship(
        "College",
        back_populates="aliases",
    )

    __table_args__ = (
        Index(
            "ix_college_aliases_college_id",
            "college_id",
        ),
    )


Index(
    "uq_college_aliases_alias_ci",
    func.lower(func.trim(CollegeAlias.alias)),
    unique=True,
)


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
# FACULTY ACCESS / ROLE HISTORY
# --------------------------------------------------


class FacultyAccessHistory(Base):
    __tablename__ = "faculty_access_history"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    previous_role: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    new_role: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    previous_parent_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    new_parent_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
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

    changed_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
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

    faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[faculty_id],
    )

    previous_parent_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[previous_parent_faculty_id],
    )

    new_parent_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[new_parent_faculty_id],
    )

    changed_by_faculty: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        foreign_keys=[changed_by_faculty_id],
    )

    changed_by_admin: Mapped[Optional["Admin"]] = relationship(
        "Admin",
        foreign_keys=[changed_by_admin_id],
    )

    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[department_id],
    )

    __table_args__ = (
        Index(
            "ix_faculty_access_history_faculty_created",
            "faculty_id",
            "created_at",
        ),
        Index(
            "ix_faculty_access_history_changed_by_faculty",
            "changed_by_faculty_id",
        ),
        Index(
            "ix_faculty_access_history_changed_by_admin",
            "changed_by_admin_id",
        ),
        Index(
            "ix_faculty_access_history_parent",
            "new_parent_faculty_id",
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


# --------------------------------------------------
# BATCH FINAL EXPORT JOB
# --------------------------------------------------


class BatchExportJob(Base):
    """
    Durable metadata for one final academic-batch export.

    The ZIP itself belongs in MinIO / AWS S3.

    This row stores:
    - immutable export scope
    - storage location
    - SHA-256 integrity evidence
    - exported row counts
    - verification timestamp

    A verified export is the future prerequisite for:

        GRADUATED -> ARCHIVED

    This model never represents Student deletion or purge.
    """

    __tablename__ = "batch_export_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # Historical organization snapshot.
    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # NULL only for a College Coordinator whole-college export.
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    # Matches FacultyScopeType.value:
    #     college
    #     department
    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # SET NULL preserves the export record if the Faculty account
    # is ever removed.
    requested_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default=text(
            "'PENDING'"
        ),
    )

    # =====================================================
    # OBJECT STORAGE
    # =====================================================

    storage_provider: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    bucket: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    object_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    sha256: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    # =====================================================
    # EXPORTED ROW ACCOUNTING
    # =====================================================

    students_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    academic_history_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_records_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_points_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    event_participation_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    certificates_rows: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # =====================================================
    # PROCESS STATE / VERIFICATION
    # =====================================================

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # IMPORTANT:
    # Archive must later require verified_at IS NOT NULL.
    #
    # Merely having status=COMPLETED is not sufficient.
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    failure_reason: Mapped[Optional[str]] = mapped_column(
        Text,
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
            """
            scope_type IN (
                'college',
                'department'
            )
            """,
            name="ck_batch_export_jobs_scope_type",
        ),

        CheckConstraint(
            """
            (
                scope_type = 'college'
                AND department_id IS NULL
            )
            OR
            (
                scope_type = 'department'
                AND department_id IS NOT NULL
            )
            """,
            name="ck_batch_export_jobs_scope_department",
        ),

        CheckConstraint(
            """
            status IN (
                'PENDING',
                'PROCESSING',
                'COMPLETED',
                'FAILED'
            )
            """,
            name="ck_batch_export_jobs_status",
        ),

        CheckConstraint(
            """
            students_rows >= 0
            AND academic_history_rows >= 0
            AND activity_records_rows >= 0
            AND activity_points_rows >= 0
            AND event_participation_rows >= 0
            AND certificates_rows >= 0
            """,
            name="ck_batch_export_jobs_row_counts",
        ),

        CheckConstraint(
            """
            file_size_bytes IS NULL
            OR file_size_bytes >= 0
            """,
            name="ck_batch_export_jobs_file_size",
        ),

        # A completed export must have durable integrity metadata.
        CheckConstraint(
            """
            status <> 'COMPLETED'
            OR (
                storage_provider IS NOT NULL
                AND bucket IS NOT NULL
                AND object_key IS NOT NULL
                AND sha256 IS NOT NULL
                AND file_size_bytes IS NOT NULL
                AND completed_at IS NOT NULL
            )
            """,
            name="ck_batch_export_jobs_completed_metadata",
        ),

        # Verification can only exist for a completed export.
        CheckConstraint(
            """
            verified_at IS NULL
            OR status = 'COMPLETED'
            """,
            name="ck_batch_export_jobs_verified_status",
        ),

        Index(
            "ix_batch_export_jobs_batch_scope_status_created",
            "batch_id",
            "scope_type",
            "department_id",
            "status",
            "created_at",
        ),

        Index(
            "ix_batch_export_jobs_verified_gate",
            "batch_id",
            "scope_type",
            "department_id",
            "verified_at",
        ),
    )


# --------------------------------------------------
# BATCH PURGE JOB
# --------------------------------------------------


class BatchPurgeJob(Base):
    """
    Durable metadata for one academic-batch purge operation.

    Purge lifecycle:

        ARCHIVED -> PURGED

    Important:
    - Student identity rows remain.
    - Certificates remain.
    - EventSubmission rows remain.
    - EventParticipant/history rows remain.
    - StudentAcademicHistory remains.
    - immutable AuditLog rows remain.
    - BatchExportJob evidence remains.

    Only explicitly approved heavy / operational records may be
    physically removed by the future purge worker.

    This model itself performs no purge.
    """

    __tablename__ = "batch_purge_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "academic_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # Historical organization snapshot.
    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # NULL only for College Coordinator scope.
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "departments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Exact verified export used as purge evidence.
    export_job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "batch_export_jobs.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    requested_by_faculty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "faculty.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default=text(
            "'PENDING'"
        ),
    )

    # =====================================================
    # STUDENT ACCOUNTING
    # =====================================================

    students_targeted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    students_purged: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # =====================================================
    # DATABASE ROW ACCOUNTING
    # =====================================================

    activity_sessions_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_photos_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_face_checks_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    event_submission_photos_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    face_embeddings_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    face_enrollment_images_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    push_devices_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    notification_deliveries_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    event_role_assignments_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    faculty_assignments_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_progress_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    activity_stats_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    point_adjustments_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # =====================================================
    # STORAGE ACCOUNTING
    # =====================================================

    storage_objects_targeted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    storage_objects_deleted: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    ignored_storage_metadata: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    unsafe_storage_references: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # =====================================================
    # DURABLE PROCESS CHECKPOINTS
    # =====================================================

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Set only after all validated object-storage deletion work
    # has completed successfully.
    storage_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Set only after operational PostgreSQL cleanup and
    # ARCHIVED -> PURGED transitions have completed.
    database_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    failure_reason: Mapped[Optional[str]] = mapped_column(
        Text,
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
            """
            scope_type IN (
                'college',
                'department'
            )
            """,
            name="ck_batch_purge_jobs_scope_type",
        ),

        CheckConstraint(
            """
            (
                scope_type = 'college'
                AND department_id IS NULL
            )
            OR
            (
                scope_type = 'department'
                AND department_id IS NOT NULL
            )
            """,
            name="ck_batch_purge_jobs_scope_department",
        ),

        CheckConstraint(
            """
            status IN (
                'PENDING',
                'PROCESSING',
                'COMPLETED',
                'FAILED'
            )
            """,
            name="ck_batch_purge_jobs_status",
        ),

        CheckConstraint(
            """
            students_targeted >= 0
            AND students_purged >= 0

            AND activity_sessions_deleted >= 0
            AND activity_photos_deleted >= 0
            AND activity_face_checks_deleted >= 0
            AND event_submission_photos_deleted >= 0

            AND face_embeddings_deleted >= 0
            AND face_enrollment_images_deleted >= 0

            AND push_devices_deleted >= 0
            AND notification_deliveries_deleted >= 0

            AND event_role_assignments_deleted >= 0
            AND faculty_assignments_deleted >= 0

            AND activity_progress_deleted >= 0
            AND activity_stats_deleted >= 0
            AND point_adjustments_deleted >= 0

            AND storage_objects_targeted >= 0
            AND storage_objects_deleted >= 0
            AND ignored_storage_metadata >= 0
            AND unsafe_storage_references >= 0
            """,
            name="ck_batch_purge_jobs_counts",
        ),

        CheckConstraint(
            """
            students_purged <= students_targeted
            """,
            name="ck_batch_purge_jobs_student_counts",
        ),

        CheckConstraint(
            """
            storage_objects_deleted
            <= storage_objects_targeted
            """,
            name="ck_batch_purge_jobs_storage_counts",
        ),

        CheckConstraint(
            """
            status <> 'COMPLETED'
            OR (
                started_at IS NOT NULL
                AND storage_completed_at IS NOT NULL
                AND database_completed_at IS NOT NULL
                AND completed_at IS NOT NULL
                AND unsafe_storage_references = 0
                AND students_purged = students_targeted
            )
            """,
            name="ck_batch_purge_jobs_completed_state",
        ),

        Index(
            "ix_batch_purge_jobs_batch_scope_status_created",
            "batch_id",
            "scope_type",
            "department_id",
            "status",
            "created_at",
        ),

        Index(
            "ix_batch_purge_jobs_export_job",
            "export_job_id",
            "created_at",
        ),
    )
