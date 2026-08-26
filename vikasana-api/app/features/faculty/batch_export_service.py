from __future__ import annotations

import asyncio
import csv
import enum
import hashlib
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from app.core.batch_export_storage import (
    build_batch_export_object_key,
    upload_and_verify_batch_export,
)

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.features.faculty.batch_export_contract import (
    BATCH_EXPORT_CSV_FILENAMES,
    ACADEMIC_HISTORY_COLUMNS,
    ACTIVITY_POINTS_COLUMNS,
    ACTIVITY_RECORDS_COLUMNS,
    CERTIFICATES_COLUMNS,
    EVENT_PARTICIPATION_COLUMNS,
    STUDENTS_COLUMNS,
)
from app.features.activities.models import (
    ActivityFaceCheck,
    ActivityPhoto,
    ActivitySession,
    ActivityType,
    StudentActivityStats,
    StudentPointAdjustment,
)

from app.features.certificates.models import Certificate

from app.features.faculty.models import Faculty
from app.features.faculty.permission_scope import (
    FacultyScopeType,
    ROLE_COLLEGE_COORDINATOR,
    ROLE_HOD,
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.organization.models import (
    AcademicBatch,
    BatchExportJob,
    Department,
    StudentAcademicHistory,
)
from app.features.students.models import Student
from app.features.events.models import (
    Event,
    EventActivityType,
    EventParticipant,
    EventRoleAssignment,
    EventSubmission,
)


STUDENTS_EXPORT_CHUNK_SIZE = 1000
MAX_STUDENTS_EXPORT_CHUNK_SIZE = 5000


def _normalized_college_key(
    value: str,
) -> str:
    normalized = str(
        value or ""
    ).strip().lower()

    if not normalized:
        raise ValueError(
            "College scope is required"
        )

    return normalized


def _csv_value(
    value,
) -> str:
    """
    Convert DB values into deterministic CSV-safe scalar text.

    Important:
    - NULL -> empty string
    - booleans -> true / false
    - datetime/date/time -> ISO-8601
    - Enum -> its stored value
    """

    if value is None:
        return ""

    if isinstance(
        value,
        bool,
    ):
        return (
            "true"
            if value
            else "false"
        )

    if isinstance(
        value,
        enum.Enum,
    ):
        return str(
            value.value
        )

    if isinstance(
        value,
        (
            datetime,
            date,
            time,
        ),
    ):
        return value.isoformat()

    return str(
        value
    )


async def _resolve_export_batch(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> tuple[int, str]:
    """
    Resolve an active AcademicBatch inside the authenticated college.

    Department scope is applied later to Student rows because
    AcademicBatch itself is intentionally college-wide.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    statement = (
        select(
            AcademicBatch.id,
            AcademicBatch.name,
        )
        .where(
            AcademicBatch.id == batch_id,
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key,
            AcademicBatch.is_active.is_(
                True
            ),
        )
    )

    result = await db.execute(
        statement
    )

    row = result.one_or_none()

    if row is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    return (
        int(row.id),
        str(row.name),
    )


async def write_students_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = STUDENTS_EXPORT_CHUNK_SIZE,
) -> int:
    """
    Write students.csv for one authenticated Website Faculty scope.

    READ ONLY.

    Scalability:
    - keyset cursor on Student.id
    - bounded chunks
    - no OFFSET pagination
    - no overall result ceiling
    - no all-student ID materialization

    The caller owns destination lifecycle.

    Returns:
        Number of Student rows written, excluding the CSV header.
    """

    if export_job_id < 1:
        raise ValueError(
            "export_job_id must be greater than zero"
        )

    chunk_size = int(
        chunk_size
    )

    if not (
        1
        <= chunk_size
        <= MAX_STUDENTS_EXPORT_CHUNK_SIZE
    ):
        raise ValueError(
            "Invalid students export chunk size"
        )

    (
        resolved_batch_id,
        batch_name,
    ) = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    writer = csv.writer(
        destination,
        lineterminator="\n",
    )

    writer.writerow(
        STUDENTS_COLUMNS
    )

    exported_count = 0
    cursor_id = 0

    while True:
        statement = (
            select(
                Student.id.label(
                    "student_id"
                ),
                Student.college.label(
                    "college"
                ),
                Student.name.label(
                    "name"
                ),
                Student.usn.label(
                    "usn"
                ),
                Student.branch.label(
                    "branch"
                ),
                Student.email.label(
                    "email"
                ),
                Student.student_type.label(
                    "student_type"
                ),
                Student.is_active.label(
                    "is_active"
                ),
                Student.lifecycle_status.label(
                    "lifecycle_status"
                ),
                Student.graduated_at.label(
                    "graduated_at"
                ),
                Student.archived_at.label(
                    "archived_at"
                ),
                Student.purged_at.label(
                    "purged_at"
                ),
                Student.required_total_points.label(
                    "required_total_points"
                ),
                Student.total_points_earned.label(
                    "total_points_earned"
                ),
                Student.face_enrolled.label(
                    "face_enrolled"
                ),
                Student.face_enrolled_at.label(
                    "face_enrolled_at"
                ),
                Student.passout_year.label(
                    "passout_year"
                ),
                Student.admitted_year.label(
                    "admitted_year"
                ),
                Student.department_id.label(
                    "department_id"
                ),
                Department.name.label(
                    "department_name"
                ),
                Department.code.label(
                    "department_code"
                ),
                Student.batch_id.label(
                    "batch_id"
                ),
                Student.current_year.label(
                    "current_year"
                ),
                Student.assigned_faculty_id.label(
                    "assigned_faculty_id"
                ),
                Faculty.full_name.label(
                    "assigned_faculty_name"
                ),
                Faculty.email.label(
                    "assigned_faculty_email"
                ),
                Student.created_at.label(
                    "created_at"
                ),
                Student.created_by_faculty_id.label(
                    "created_by_faculty_id"
                ),
            )
            .select_from(
                Student
            )
            .outerjoin(
                Department,
                Department.id
                == Student.department_id,
            )
            .outerjoin(
                Faculty,
                Faculty.id
                == Student.assigned_faculty_id,
            )
            .where(
                Student.batch_id
                == resolved_batch_id,
                Student.id
                > cursor_id,
            )
            .order_by(
                Student.id.asc()
            )
            .limit(
                chunk_size
            )
        )

        # Authoritative security boundary:
        # Coordinator -> same college
        # HOD         -> same college + exact department
        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        result = await db.execute(
            statement
        )

        rows = result.all()

        if not rows:
            break

        for row in rows:
            writer.writerow(
                tuple(
                    _csv_value(value)
                    for value in (
                        export_job_id,
                        resolved_batch_id,
                        scope.scope_type.value,
                        scope.department_id,

                        row.student_id,
                        row.college,
                        row.name,
                        row.usn,
                        row.branch,
                        row.email,
                        row.student_type,

                        row.is_active,
                        row.lifecycle_status,
                        row.graduated_at,
                        row.archived_at,
                        row.purged_at,

                        row.required_total_points,
                        row.total_points_earned,

                        row.face_enrolled,
                        row.face_enrolled_at,

                        row.passout_year,
                        row.admitted_year,

                        row.department_id,
                        row.department_name,
                        row.department_code,

                        resolved_batch_id,
                        batch_name,

                        row.current_year,

                        row.assigned_faculty_id,
                        row.assigned_faculty_name,
                        row.assigned_faculty_email,

                        row.created_at,
                        row.created_by_faculty_id,
                    )
                )
            )

        exported_count += len(
            rows
        )

        cursor_id = int(
            rows[-1].student_id
        )

    return exported_count



ACADEMIC_HISTORY_EXPORT_CHUNK_SIZE = 1000


async def write_academic_history_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = ACADEMIC_HISTORY_EXPORT_CHUNK_SIZE,
) -> int:
    """Write scoped academic_history.csv using keyset pagination."""

    if export_job_id < 1:
        raise ValueError("export_job_id must be greater than zero")

    chunk_size = int(chunk_size)

    if not 1 <= chunk_size <= 5000:
        raise ValueError("Invalid academic history export chunk size")

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    FromDepartment = aliased(Department)
    ToDepartment = aliased(Department)
    FromBatch = aliased(AcademicBatch)
    ToBatch = aliased(AcademicBatch)

    writer = csv.writer(destination, lineterminator="\n")
    writer.writerow(ACADEMIC_HISTORY_COLUMNS)

    cursor_id = 0
    exported_count = 0

    while True:
        statement = (
            select(
                StudentAcademicHistory.id.label("history_id"),
                StudentAcademicHistory.student_id,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),

                StudentAcademicHistory.action,

                StudentAcademicHistory.from_department_id,
                FromDepartment.name.label("from_department_name"),

                StudentAcademicHistory.to_department_id,
                ToDepartment.name.label("to_department_name"),

                StudentAcademicHistory.from_batch_id,
                FromBatch.name.label("from_batch_name"),

                StudentAcademicHistory.to_batch_id,
                ToBatch.name.label("to_batch_name"),

                StudentAcademicHistory.from_year,
                StudentAcademicHistory.to_year,

                StudentAcademicHistory.academic_session,
                StudentAcademicHistory.reason,

                StudentAcademicHistory.changed_by_faculty_id,
                StudentAcademicHistory.changed_by_admin_id,

                StudentAcademicHistory.created_at,
            )
            .select_from(StudentAcademicHistory)
            .join(
                Student,
                Student.id == StudentAcademicHistory.student_id,
            )
            .outerjoin(
                FromDepartment,
                FromDepartment.id
                == StudentAcademicHistory.from_department_id,
            )
            .outerjoin(
                ToDepartment,
                ToDepartment.id
                == StudentAcademicHistory.to_department_id,
            )
            .outerjoin(
                FromBatch,
                FromBatch.id
                == StudentAcademicHistory.from_batch_id,
            )
            .outerjoin(
                ToBatch,
                ToBatch.id
                == StudentAcademicHistory.to_batch_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                StudentAcademicHistory.id > cursor_id,
            )
            .order_by(StudentAcademicHistory.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                row.history_id,

                row.student_id,
                row.student_usn,
                row.student_name,

                row.action,

                row.from_department_id,
                row.from_department_name,

                row.to_department_id,
                row.to_department_name,

                row.from_batch_id,
                row.from_batch_name,

                row.to_batch_id,
                row.to_batch_name,

                row.from_year,
                row.to_year,

                row.academic_session,
                row.reason,

                row.changed_by_faculty_id,
                row.changed_by_admin_id,

                row.created_at,
            )

            writer.writerow(
                tuple(_csv_value(v) for v in values)
            )

        exported_count += len(rows)
        cursor_id = int(rows[-1].history_id)

    return exported_count



ACTIVITY_RECORDS_EXPORT_CHUNK_SIZE = 1000


async def write_activity_records_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = ACTIVITY_RECORDS_EXPORT_CHUNK_SIZE,
) -> int:
    """
    Export SESSION + PHOTO + FACE_CHECK evidence.

    Read-only, scoped and keyset paginated.
    """

    if export_job_id < 1:
        raise ValueError("export_job_id must be greater than zero")

    chunk_size = int(chunk_size)
    if not 1 <= chunk_size <= 5000:
        raise ValueError("Invalid activity records export chunk size")

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    writer = csv.writer(destination, lineterminator="\n")
    writer.writerow(ACTIVITY_RECORDS_COLUMNS)

    exported_count = 0

    # =====================================================
    # SESSION
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                ActivitySession,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                ActivityType.name.label("activity_type_name"),
                Event.title.label("event_title"),
            )
            .join(Student, Student.id == ActivitySession.student_id)
            .join(
                ActivityType,
                ActivityType.id == ActivitySession.activity_type_id,
            )
            .outerjoin(Event, Event.id == ActivitySession.event_id)
            .where(
                Student.batch_id == resolved_batch_id,
                ActivitySession.id > cursor_id,
            )
            .order_by(ActivitySession.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(statement, scope)
        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            s = row.ActivitySession

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "SESSION",
                s.id,

                s.student_id,
                row.student_usn,
                row.student_name,

                s.id,

                s.activity_type_id,
                row.activity_type_name,

                s.event_id,
                row.event_title,
                s.event_submission_id,

                s.status,
                s.activity_name,
                s.description,
                s.session_code,

                s.started_at,
                s.expires_at,
                s.submitted_at,

                s.duration_hours,
                s.flag_reason,
                s.points_awarded_at,

                None, None, None,
                None, None, None, None,
                None, None, None,

                None, None, None, None,
                None, None, None,

                s.created_at,
                None,
            )

            writer.writerow(tuple(_csv_value(v) for v in values))

        exported_count += len(rows)
        cursor_id = int(rows[-1].ActivitySession.id)

    # =====================================================
    # PHOTO
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                ActivityPhoto,
                ActivitySession,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                ActivityType.name.label("activity_type_name"),
                Event.title.label("event_title"),
            )
            .join(
                ActivitySession,
                ActivitySession.id == ActivityPhoto.session_id,
            )
            .join(Student, Student.id == ActivityPhoto.student_id)
            .join(
                ActivityType,
                ActivityType.id == ActivitySession.activity_type_id,
            )
            .outerjoin(Event, Event.id == ActivitySession.event_id)
            .where(
                Student.batch_id == resolved_batch_id,
                ActivityPhoto.id > cursor_id,
            )
            .order_by(ActivityPhoto.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(statement, scope)
        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            p = row.ActivityPhoto
            s = row.ActivitySession

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "PHOTO",
                p.id,

                p.student_id,
                row.student_usn,
                row.student_name,

                s.id,

                s.activity_type_id,
                row.activity_type_name,

                s.event_id,
                row.event_title,
                s.event_submission_id,

                s.status,
                s.activity_name,
                s.description,
                s.session_code,

                s.started_at,
                s.expires_at,
                s.submitted_at,

                s.duration_hours,
                s.flag_reason,
                s.points_awarded_at,

                p.id,
                p.seq_no,
                p.image_url,

                p.lat,
                p.lng,
                p.captured_at,
                p.sha256,

                p.distance_m,
                p.is_in_geofence,
                p.geo_flag_reason,

                None, None, None, None,
                None, None, None,

                p.created_at,
                None,
            )

            writer.writerow(tuple(_csv_value(v) for v in values))

        exported_count += len(rows)
        cursor_id = int(rows[-1].ActivityPhoto.id)

    # =====================================================
    # FACE CHECK
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                ActivityFaceCheck,
                ActivityPhoto,
                ActivitySession,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                ActivityType.name.label("activity_type_name"),
                Event.title.label("event_title"),
            )
            .join(
                ActivitySession,
                ActivitySession.id == ActivityFaceCheck.session_id,
            )
            .join(
                ActivityPhoto,
                ActivityPhoto.id == ActivityFaceCheck.photo_id,
            )
            .join(Student, Student.id == ActivityFaceCheck.student_id)
            .join(
                ActivityType,
                ActivityType.id == ActivitySession.activity_type_id,
            )
            .outerjoin(Event, Event.id == ActivitySession.event_id)
            .where(
                Student.batch_id == resolved_batch_id,
                ActivityFaceCheck.id > cursor_id,
            )
            .order_by(ActivityFaceCheck.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(statement, scope)
        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            f = row.ActivityFaceCheck
            p = row.ActivityPhoto
            s = row.ActivitySession

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "FACE_CHECK",
                f.id,

                f.student_id,
                row.student_usn,
                row.student_name,

                s.id,

                s.activity_type_id,
                row.activity_type_name,

                s.event_id,
                row.event_title,
                s.event_submission_id,

                s.status,
                s.activity_name,
                s.description,
                s.session_code,

                s.started_at,
                s.expires_at,
                s.submitted_at,

                s.duration_hours,
                s.flag_reason,
                s.points_awarded_at,

                p.id,
                p.seq_no,
                p.image_url,

                p.lat,
                p.lng,
                p.captured_at,
                p.sha256,

                p.distance_m,
                p.is_in_geofence,
                p.geo_flag_reason,

                f.id,
                f.matched,
                f.cosine_score,
                f.l2_score,
                f.total_faces,
                f.processed_object,
                f.reason,

                f.created_at,
                f.updated_at,
            )

            writer.writerow(tuple(_csv_value(v) for v in values))

        exported_count += len(rows)
        cursor_id = int(rows[-1].ActivityFaceCheck.id)

    return exported_count



ACTIVITY_POINTS_EXPORT_CHUNK_SIZE = 1000


async def _event_activity_type_export_map(
    *,
    db: AsyncSession,
    event_ids: list[int],
) -> dict[int, tuple[str, str]]:
    """
    Load Event -> ActivityType mappings only for the current bounded
    export chunk.

    Returns:
        event_id -> (
            semicolon-separated activity_type_ids,
            semicolon-separated activity_type_names,
        )
    """

    clean_ids = sorted({
        int(event_id)
        for event_id in event_ids
        if event_id is not None
    })

    if not clean_ids:
        return {}

    statement = (
        select(
            EventActivityType.event_id,
            EventActivityType.activity_type_id,
            ActivityType.name.label("activity_type_name"),
        )
        .join(
            ActivityType,
            ActivityType.id == EventActivityType.activity_type_id,
        )
        .where(
            EventActivityType.event_id.in_(clean_ids)
        )
        .order_by(
            EventActivityType.event_id.asc(),
            EventActivityType.activity_type_id.asc(),
        )
    )

    rows = (await db.execute(statement)).all()

    grouped: dict[int, list[tuple[int, str]]] = {}

    for row in rows:
        grouped.setdefault(
            int(row.event_id),
            [],
        ).append(
            (
                int(row.activity_type_id),
                str(row.activity_type_name or ""),
            )
        )

    return {
        event_id: (
            ";".join(str(item[0]) for item in items),
            ";".join(item[1] for item in items),
        )
        for event_id, items in grouped.items()
    }


def _session_id_from_adjustment_reason(
    reason: str | None,
) -> int | None:
    """
    AUTO_AWARD_SESSION_123 -> 123.

    Other/manual adjustments intentionally return NULL.
    """

    if not reason:
        return None

    match = re.fullmatch(
        r"AUTO_AWARD_SESSION_(\d+)",
        str(reason).strip(),
    )

    if not match:
        return None

    return int(match.group(1))


async def write_activity_points_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = ACTIVITY_POINTS_EXPORT_CHUNK_SIZE,
) -> int:
    """
    Write the authoritative point export.

    record_type:
    - EVENT_CREDIT
    - POINT_ADJUSTMENT
    - ACTIVITY_TYPE_SNAPSHOT

    Important:
    StudentActivityStats is exported as an aggregate snapshot,
    never as another transaction.

    No point calculation occurs in this function.
    """

    if export_job_id < 1:
        raise ValueError(
            "export_job_id must be greater than zero"
        )

    chunk_size = int(chunk_size)

    if not 1 <= chunk_size <= 5000:
        raise ValueError(
            "Invalid activity points export chunk size"
        )

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    writer = csv.writer(
        destination,
        lineterminator="\n",
    )

    writer.writerow(
        ACTIVITY_POINTS_COLUMNS
    )

    exported_count = 0


    # =====================================================
    # 1. EVENT CREDIT TRANSACTIONS
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                EventSubmission,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                Event.title.label("event_title"),
            )
            .join(
                Student,
                Student.id == EventSubmission.student_id,
            )
            .outerjoin(
                Event,
                Event.id == EventSubmission.event_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                EventSubmission.points_credited.is_(True),
                EventSubmission.id > cursor_id,
            )
            .order_by(
                EventSubmission.id.asc()
            )
            .limit(
                chunk_size
            )
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (
            await db.execute(statement)
        ).all()

        if not rows:
            break

        mapping = await _event_activity_type_export_map(
            db=db,
            event_ids=[
                row.EventSubmission.event_id
                for row in rows
                if row.EventSubmission.event_id is not None
            ],
        )

        for row in rows:
            submission = row.EventSubmission

            mapped_ids, mapped_names = mapping.get(
                int(submission.event_id)
                if submission.event_id is not None
                else -1,
                ("", ""),
            )

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "EVENT_CREDIT",
                submission.id,

                submission.student_id,
                row.student_usn,
                row.student_name,

                # Per-type allocation is NOT persisted.
                None,
                None,

                mapped_ids,
                mapped_names,

                submission.event_id,
                row.event_title,
                submission.id,

                None,

                submission.awarded_points,

                None,
                None,

                None,

                submission.points_credited,
                submission.status,

                None,
                None,
                None,
                None,

                None,

                # No exact standalone credited_at column exists
                # on EventSubmission, so do not fabricate one.
                None,

                submission.created_at,
                None,
            )

            writer.writerow(
                tuple(
                    _csv_value(v)
                    for v in values
                )
            )

        exported_count += len(rows)
        cursor_id = int(
            rows[-1].EventSubmission.id
        )


    # =====================================================
    # 2. POINT ADJUSTMENT TRANSACTIONS
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                StudentPointAdjustment,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                ActivityType.name.label("activity_type_name"),
            )
            .join(
                Student,
                Student.id == StudentPointAdjustment.student_id,
            )
            .outerjoin(
                ActivityType,
                ActivityType.id
                == StudentPointAdjustment.activity_type_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                StudentPointAdjustment.id > cursor_id,
            )
            .order_by(
                StudentPointAdjustment.id.asc()
            )
            .limit(
                chunk_size
            )
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (
            await db.execute(statement)
        ).all()

        if not rows:
            break

        for row in rows:
            adjustment = row.StudentPointAdjustment

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "POINT_ADJUSTMENT",
                adjustment.id,

                adjustment.student_id,
                row.student_usn,
                row.student_name,

                adjustment.activity_type_id,
                row.activity_type_name,

                None,
                None,

                None,
                None,
                None,

                _session_id_from_adjustment_reason(
                    adjustment.reason
                ),

                adjustment.delta_points,

                None,
                None,

                adjustment.new_total_points,

                None,
                adjustment.status,

                adjustment.reason,
                adjustment.category,
                adjustment.activity_name,
                adjustment.activity_date,

                adjustment.created_by_admin_id,

                None,
                adjustment.created_at,
                None,
            )

            writer.writerow(
                tuple(
                    _csv_value(v)
                    for v in values
                )
            )

        exported_count += len(rows)
        cursor_id = int(
            rows[-1].StudentPointAdjustment.id
        )


    # =====================================================
    # 3. FINAL ACTIVITY-TYPE SNAPSHOT
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                StudentActivityStats,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                ActivityType.name.label("activity_type_name"),
            )
            .join(
                Student,
                Student.id == StudentActivityStats.student_id,
            )
            .join(
                ActivityType,
                ActivityType.id
                == StudentActivityStats.activity_type_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                StudentActivityStats.id > cursor_id,
            )
            .order_by(
                StudentActivityStats.id.asc()
            )
            .limit(
                chunk_size
            )
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (
            await db.execute(statement)
        ).all()

        if not rows:
            break

        for row in rows:
            stats = row.StudentActivityStats

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "ACTIVITY_TYPE_SNAPSHOT",
                stats.id,

                stats.student_id,
                row.student_usn,
                row.student_name,

                stats.activity_type_id,
                row.activity_type_name,

                None,
                None,

                None,
                None,
                None,

                None,

                # Snapshot is NOT a transaction.
                None,

                stats.points_awarded,
                stats.total_verified_hours,

                None,

                None,
                "SNAPSHOT",

                None,
                None,
                None,
                None,

                None,

                None,
                None,
                stats.updated_at,
            )

            writer.writerow(
                tuple(
                    _csv_value(v)
                    for v in values
                )
            )

        exported_count += len(rows)
        cursor_id = int(
            rows[-1].StudentActivityStats.id
        )

    return exported_count



EVENT_PARTICIPATION_EXPORT_CHUNK_SIZE = 1000


async def write_event_participation_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = EVENT_PARTICIPATION_EXPORT_CHUNK_SIZE,
) -> int:
    """
    Export:
    - EVENT_SUBMISSION
    - EVENT_PARTICIPANT
    - ROLE_ASSIGNMENT

    External participant snapshots are included when that historical
    participant is linked to a Student belonging to this batch/scope.

    EventRoleAssignment has only exclusive_group_key, not event_id,
    so the export does not invent an Event relationship.
    """

    if export_job_id < 1:
        raise ValueError("export_job_id must be greater than zero")

    chunk_size = int(chunk_size)

    if not 1 <= chunk_size <= 5000:
        raise ValueError(
            "Invalid event participation export chunk size"
        )

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    writer = csv.writer(
        destination,
        lineterminator="\n",
    )
    writer.writerow(EVENT_PARTICIPATION_COLUMNS)

    exported_count = 0


    # =====================================================
    # EVENT SUBMISSIONS
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                EventSubmission,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                Event.title.label("event_title"),
                Event.event_date.label("event_date"),
                Event.event_role.label("event_role"),
                Event.exclusive_group_key.label("exclusive_group_key"),
            )
            .join(
                Student,
                Student.id == EventSubmission.student_id,
            )
            .outerjoin(
                Event,
                Event.id == EventSubmission.event_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                EventSubmission.id > cursor_id,
            )
            .order_by(EventSubmission.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            s = row.EventSubmission

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "EVENT_SUBMISSION",
                s.id,

                s.event_id,
                row.event_title,
                row.event_date,
                row.event_role,
                row.exclusive_group_key,

                s.student_id,
                row.student_usn,
                row.student_name,

                s.event_participant_id,

                None, None,
                None, None, None, None, None,
                None, None, None, None,
                None, None, None,

                s.status,
                s.description,
                s.submitted_at,
                s.approved_at,
                s.rejection_reason,
                s.awarded_points,
                s.points_credited,

                None,

                s.created_at,
                None,
            )

            writer.writerow(
                tuple(_csv_value(v) for v in values)
            )

        exported_count += len(rows)
        cursor_id = int(rows[-1].EventSubmission.id)


    # =====================================================
    # HISTORICAL EVENT PARTICIPANTS
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                EventParticipant,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
                Event.title.label("event_title"),
                Event.event_date.label("event_date"),
                Event.event_role.label("event_role"),
                Event.exclusive_group_key.label("exclusive_group_key"),
            )
            .join(
                Student,
                Student.id == EventParticipant.student_id,
            )
            .join(
                Event,
                Event.id == EventParticipant.event_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                EventParticipant.id > cursor_id,
            )
            .order_by(EventParticipant.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            p = row.EventParticipant

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "EVENT_PARTICIPANT",
                p.id,

                p.event_id,
                row.event_title,
                row.event_date,
                row.event_role,
                row.exclusive_group_key,

                p.student_id,
                row.student_usn,
                row.student_name,

                p.id,

                p.participant_type,
                p.status,

                p.name_snapshot,
                p.email_snapshot,
                p.phone_snapshot,
                p.usn_snapshot,
                p.institution_name_snapshot,

                p.email_normalized,
                p.phone_normalized,
                p.usn_normalized,
                p.institution_name_normalized,

                p.source_fingerprint,
                p.import_batch_id,
                p.created_by_admin_id,

                None, None, None, None, None, None, None,

                None,

                p.created_at,
                p.linked_at,
            )

            writer.writerow(
                tuple(_csv_value(v) for v in values)
            )

        exported_count += len(rows)
        cursor_id = int(rows[-1].EventParticipant.id)


    # =====================================================
    # PARTICIPANT / VOLUNTEER ROLE ASSIGNMENTS
    # =====================================================

    cursor_id = 0

    while True:
        statement = (
            select(
                EventRoleAssignment,
                Student.usn.label("student_usn"),
                Student.name.label("student_name"),
            )
            .join(
                Student,
                Student.id == EventRoleAssignment.student_id,
            )
            .where(
                Student.batch_id == resolved_batch_id,
                EventRoleAssignment.id > cursor_id,
            )
            .order_by(EventRoleAssignment.id.asc())
            .limit(chunk_size)
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (await db.execute(statement)).all()

        if not rows:
            break

        for row in rows:
            a = row.EventRoleAssignment

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                "ROLE_ASSIGNMENT",
                a.id,

                None,
                None,
                None,
                None,
                a.exclusive_group_key,

                a.student_id,
                row.student_usn,
                row.student_name,

                None,

                None, None,
                None, None, None, None, None,
                None, None, None, None,
                None, None, None,

                None, None, None, None, None, None, None,

                a.role_allowed,

                a.created_at,
                None,
            )

            writer.writerow(
                tuple(_csv_value(v) for v in values)
            )

        exported_count += len(rows)
        cursor_id = int(rows[-1].EventRoleAssignment.id)

    return exported_count



CERTIFICATES_EXPORT_CHUNK_SIZE = 1000


async def write_certificates_csv(
    *,
    db: AsyncSession,
    destination: TextIO,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    chunk_size: int = CERTIFICATES_EXPORT_CHUNK_SIZE,
) -> int:
    """
    Write certificates.csv.

    Read-only.

    The CSV stores certificate metadata and pdf_path only.
    PDF bytes are not fetched or embedded.
    """

    if export_job_id < 1:
        raise ValueError(
            "export_job_id must be greater than zero"
        )

    chunk_size = int(chunk_size)

    if not 1 <= chunk_size <= 5000:
        raise ValueError(
            "Invalid certificates export chunk size"
        )

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    writer = csv.writer(
        destination,
        lineterminator="\n",
    )

    writer.writerow(
        CERTIFICATES_COLUMNS
    )

    exported_count = 0
    cursor_id = 0

    while True:
        statement = (
            select(
                Certificate,

                Student.usn.label(
                    "student_usn"
                ),

                Student.name.label(
                    "student_name"
                ),

                Event.title.label(
                    "event_title"
                ),

                ActivityType.name.label(
                    "activity_type_name"
                ),
            )
            .join(
                Student,
                Student.id
                == Certificate.student_id,
            )
            .join(
                Event,
                Event.id
                == Certificate.event_id,
            )
            .join(
                ActivityType,
                ActivityType.id
                == Certificate.activity_type_id,
            )
            .where(
                Student.batch_id
                == resolved_batch_id,

                Certificate.id
                > cursor_id,
            )
            .order_by(
                Certificate.id.asc()
            )
            .limit(
                chunk_size
            )
        )

        statement = apply_student_scope_to_statement(
            statement,
            scope,
        )

        rows = (
            await db.execute(statement)
        ).all()

        if not rows:
            break

        for row in rows:
            certificate = row.Certificate

            values = (
                export_job_id,
                resolved_batch_id,
                scope.scope_type.value,
                scope.department_id,

                certificate.id,
                certificate.certificate_no,
                certificate.issued_at,

                certificate.student_id,
                row.student_usn,
                row.student_name,

                certificate.submission_id,

                certificate.event_id,
                row.event_title,

                certificate.activity_type_id,
                row.activity_type_name,

                certificate.pdf_path,

                certificate.revoked_at,
                certificate.revoke_reason,
            )

            writer.writerow(
                tuple(
                    _csv_value(value)
                    for value in values
                )
            )

        exported_count += len(rows)

        cursor_id = int(
            rows[-1].Certificate.id
        )

    return exported_count



@dataclass(frozen=True, slots=True)
class BatchExportZipBuildResult:
    zip_path: str
    sha256: str
    file_size_bytes: int

    students_rows: int
    academic_history_rows: int
    activity_records_rows: int
    activity_points_rows: int
    event_participation_rows: int
    certificates_rows: int


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            block = handle.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


async def build_batch_export_zip(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
    destination_path: str | Path,
    chunk_size: int = 1000,
) -> BatchExportZipBuildResult:
    """
    Build the six-file final batch export ZIP on local disk.

    Important:
    - database reads only
    - CSV writers remain keyset/chunked
    - CSVs are written to disk, not accumulated in RAM
    - ZIP is written to disk
    - SHA-256 is calculated incrementally
    - no object-storage upload
    - no BatchExportJob mutation
    """

    if export_job_id < 1:
        raise ValueError(
            "export_job_id must be greater than zero"
        )

    destination = Path(
        destination_path
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with TemporaryDirectory(
        prefix=f"batch-export-{export_job_id}-"
    ) as temp_dir:

        temp_root = Path(
            temp_dir
        )

        writer_specs = (
            (
                "students.csv",
                write_students_csv,
            ),
            (
                "academic_history.csv",
                write_academic_history_csv,
            ),
            (
                "activity_records.csv",
                write_activity_records_csv,
            ),
            (
                "activity_points.csv",
                write_activity_points_csv,
            ),
            (
                "event_participation.csv",
                write_event_participation_csv,
            ),
            (
                "certificates.csv",
                write_certificates_csv,
            ),
        )

        counts: dict[str, int] = {}

        for (
            filename,
            writer_function,
        ) in writer_specs:

            csv_path = (
                temp_root
                / filename
            )

            with csv_path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as destination_file:

                counts[filename] = await writer_function(
                    db=db,
                    destination=destination_file,
                    scope=scope,
                    batch_id=batch_id,
                    export_job_id=export_job_id,
                    chunk_size=chunk_size,
                )

        if tuple(
            filename
            for filename, _ in writer_specs
        ) != BATCH_EXPORT_CSV_FILENAMES:
            raise RuntimeError(
                "Batch export writer contract mismatch"
            )

        with zipfile.ZipFile(
            destination,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:

            for filename in BATCH_EXPORT_CSV_FILENAMES:
                archive.write(
                    temp_root / filename,
                    arcname=filename,
                )

    file_size_bytes = (
        destination.stat().st_size
    )

    if file_size_bytes <= 0:
        raise RuntimeError(
            "Generated batch export ZIP is empty"
        )

    sha256 = _sha256_file(
        destination
    )

    if len(sha256) != 64:
        raise RuntimeError(
            "Invalid generated SHA-256"
        )

    return BatchExportZipBuildResult(
        zip_path=str(destination),
        sha256=sha256,
        file_size_bytes=file_size_bytes,

        students_rows=counts["students.csv"],
        academic_history_rows=counts["academic_history.csv"],
        activity_records_rows=counts["activity_records.csv"],
        activity_points_rows=counts["activity_points.csv"],
        event_participation_rows=counts["event_participation.csv"],
        certificates_rows=counts["certificates.csv"],
    )



def _scope_from_batch_export_job(
    job: BatchExportJob,
) -> WebsiteFacultyScope:
    """
    Rebuild the immutable server-side scope snapshot stored on the job.

    We do not trust client-supplied college/department values here.
    """

    scope_type = str(
        job.scope_type or ""
    ).strip().lower()

    if scope_type == "college":
        if job.department_id is not None:
            raise ValueError(
                "Invalid college export job scope"
            )

        return WebsiteFacultyScope(
            faculty_id=int(
                job.requested_by_faculty_id or 0
            ),
            role=ROLE_COLLEGE_COORDINATOR,
            scope_type=FacultyScopeType.COLLEGE,
            college=str(job.college),
            department_id=None,
        )

    if scope_type == "department":
        if (
            job.department_id is None
            or int(job.department_id) < 1
        ):
            raise ValueError(
                "Invalid department export job scope"
            )

        return WebsiteFacultyScope(
            faculty_id=int(
                job.requested_by_faculty_id or 0
            ),
            role=ROLE_HOD,
            scope_type=FacultyScopeType.DEPARTMENT,
            college=str(job.college),
            department_id=int(job.department_id),
        )

    raise ValueError(
        "Unsupported batch export job scope"
    )


def _safe_export_failure_reason(
    exc: Exception,
) -> str:
    """
    Keep operational failure text bounded and redact obvious secrets.
    """

    value = str(exc or "").strip()

    value = re.sub(
        r"(?i)"
        r"(access[_-]?key|secret[_-]?key|password|token)"
        r"\s*[=:]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )

    if not value:
        value = "Batch export processing failed"

    return (
        f"{type(exc).__name__}: {value}"
    )[:1000]


async def process_batch_export_job(
    *,
    db: AsyncSession,
    export_job_id: int,
    chunk_size: int = 1000,
) -> BatchExportJob:
    """
    Durable final-export state machine.

        PENDING
          -> PROCESSING
          -> build six CSV ZIP
          -> upload
          -> remote size + SHA-256 verification
          -> COMPLETED + verified_at

    Any processing exception:
        -> FAILED

    Archive is intentionally NOT performed here.
    """

    if export_job_id < 1:
        raise ValueError(
            "Invalid export_job_id"
        )

    # =====================================================
    # CLAIM PENDING JOB
    # =====================================================

    result = await db.execute(
        select(BatchExportJob)
        .where(
            BatchExportJob.id
            == export_job_id
        )
        .with_for_update()
    )

    job = result.scalar_one_or_none()

    if job is None:
        raise ValueError(
            "Batch export job not found"
        )

    if str(job.status) != "PENDING":
        raise ValueError(
            "Batch export job is not PENDING"
        )

    scope = _scope_from_batch_export_job(
        job
    )

    # Validate batch/scope before claiming the job.
    await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=int(job.batch_id),
    )

    # Snapshot immutable identifiers BEFORE commit because
    # AsyncSession expires ORM attributes on commit.
    job_batch_id = int(job.batch_id)
    job_scope_type = str(job.scope_type)
    job_department_id = (
        int(job.department_id)
        if job.department_id is not None
        else None
    )

    started_at = datetime.now(
        timezone.utc
    )

    job.status = "PROCESSING"
    job.started_at = started_at
    job.completed_at = None
    job.verified_at = None
    job.failure_reason = None

    await db.commit()

    try:
        # =================================================
        # BUILD + VERIFIED STORAGE
        # =================================================

        with TemporaryDirectory(
            prefix=f"batch-export-job-{export_job_id}-"
        ) as work_dir:

            zip_path = (
                Path(work_dir)
                / f"batch-export-{export_job_id}.zip"
            )

            # -------------------------------------------------
            # ONE CONSISTENT POSTGRESQL SNAPSHOT
            # -------------------------------------------------
            #
            # All six CSV writers must see the same database state.
            # PostgreSQL defaults to READ COMMITTED, which would allow
            # later chunks/files to observe newer committed data.
            #
            # SET TRANSACTION must be the first statement in this
            # transaction.
            await db.execute(
                text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )
            )

            build_result = await build_batch_export_zip(
                db=db,
                scope=scope,
                batch_id=job_batch_id,
                export_job_id=export_job_id,
                destination_path=zip_path,
                chunk_size=chunk_size,
            )

            # The ZIP has now been fully materialized from one
            # consistent DB snapshot. Release that snapshot BEFORE
            # potentially slow object-storage network I/O.
            await db.rollback()

            object_key = build_batch_export_object_key(
                export_job_id=export_job_id,
                batch_id=job_batch_id,
                scope_type=job_scope_type,
                department_id=job_department_id,
            )

            # MinIO SDK is synchronous. Keep network I/O off
            # the persistent worker asyncio event loop.
            verified_object = await asyncio.to_thread(
                upload_and_verify_batch_export,
                zip_path=zip_path,
                object_key=object_key,
            )

            if (
                verified_object.sha256
                != build_result.sha256
            ):
                raise RuntimeError(
                    "Verified storage checksum differs from generated ZIP"
                )

            if (
                verified_object.file_size_bytes
                != build_result.file_size_bytes
            ):
                raise RuntimeError(
                    "Verified storage size differs from generated ZIP"
                )

        # End any read transaction opened by CSV generation.
        await db.rollback()

        # =================================================
        # FINALIZE ONLY AFTER VERIFIED OBJECT EXISTS
        # =================================================

        result = await db.execute(
            select(BatchExportJob)
            .where(
                BatchExportJob.id
                == export_job_id
            )
            .with_for_update()
        )

        job = result.scalar_one_or_none()

        if job is None:
            raise RuntimeError(
                "Batch export job disappeared during processing"
            )

        if str(job.status) != "PROCESSING":
            raise RuntimeError(
                "Batch export job state changed during processing"
            )

        verified_at = datetime.now(
            timezone.utc
        )

        job.storage_provider = (
            verified_object.storage_provider
        )
        job.bucket = verified_object.bucket
        job.object_key = (
            verified_object.object_key
        )
        job.sha256 = verified_object.sha256
        job.file_size_bytes = (
            verified_object.file_size_bytes
        )

        job.students_rows = (
            build_result.students_rows
        )
        job.academic_history_rows = (
            build_result.academic_history_rows
        )
        job.activity_records_rows = (
            build_result.activity_records_rows
        )
        job.activity_points_rows = (
            build_result.activity_points_rows
        )
        job.event_participation_rows = (
            build_result.event_participation_rows
        )
        job.certificates_rows = (
            build_result.certificates_rows
        )

        job.status = "COMPLETED"
        job.completed_at = verified_at
        job.verified_at = verified_at
        job.failure_reason = None

        await db.commit()
        await db.refresh(job)

        return job

    except Exception as exc:
        await db.rollback()

        # =================================================
        # DURABLE FAILURE STATE
        # =================================================

        result = await db.execute(
            select(BatchExportJob)
            .where(
                BatchExportJob.id
                == export_job_id
            )
            .with_for_update()
        )

        failed_job = (
            result.scalar_one_or_none()
        )

        if (
            failed_job is not None
            and str(failed_job.status)
            != "COMPLETED"
        ):
            failed_job.status = "FAILED"
            failed_job.completed_at = None
            failed_job.verified_at = None
            failed_job.failure_reason = (
                _safe_export_failure_reason(
                    exc
                )
            )

            await db.commit()

        raise



async def create_batch_export_job(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> BatchExportJob:
    """
    Create one PENDING export job for the exact authenticated scope.

    Only one PENDING/PROCESSING export is allowed per
    batch + exact scope at a time.
    """

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    scope_type = scope.scope_type.value
    college_key = _normalized_college_key(
        scope.college
    )

    if scope_type == "department":
        if (
            scope.department_id is None
            or int(scope.department_id) < 1
        ):
            raise ValueError(
                "Department scope requires department_id"
            )

        department_id = int(
            scope.department_id
        )

    elif scope_type == "college":
        department_id = None

    else:
        raise ValueError(
            "Unsupported export scope"
        )

    existing_statement = (
        select(BatchExportJob.id)
        .where(
            BatchExportJob.batch_id
            == resolved_batch_id,

            func.lower(
                func.trim(
                    BatchExportJob.college
                )
            )
            == college_key,

            BatchExportJob.scope_type
            == scope_type,

            BatchExportJob.status.in_(
                (
                    "PENDING",
                    "PROCESSING",
                )
            ),
        )
        .limit(1)
    )

    if department_id is None:
        existing_statement = (
            existing_statement.where(
                BatchExportJob.department_id.is_(
                    None
                )
            )
        )
    else:
        existing_statement = (
            existing_statement.where(
                BatchExportJob.department_id
                == department_id
            )
        )

    existing_id = (
        await db.execute(
            existing_statement
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise ValueError(
            "An export is already pending or processing for this batch scope"
        )

    job = BatchExportJob(
        batch_id=resolved_batch_id,
        college=str(
            scope.college
        ).strip(),
        department_id=department_id,
        scope_type=scope_type,
        requested_by_faculty_id=(
            int(scope.faculty_id)
            if int(scope.faculty_id) > 0
            else None
        ),
        status="PENDING",
    )

    db.add(job)

    try:
        await db.flush()

    except IntegrityError as exc:
        # PostgreSQL is the final concurrency guard.
        #
        # Two requests can both pass the application-level
        # SELECT before either INSERT commits. Migration 016
        # prevents both active jobs from being persisted.
        error_text = str(
            getattr(
                exc,
                "orig",
                exc,
            )
        )

        if (
            "uq_batch_export_jobs_active_scope"
            in error_text
        ):
            raise ValueError(
                "A batch export job is already pending or processing for this scope"
            ) from exc

        raise

    return job


async def get_batch_export_job_within_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    export_job_id: int,
) -> BatchExportJob | None:
    """
    Resolve an export job only inside the exact authenticated scope.
    """

    if export_job_id < 1:
        return None

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    scope_type = scope.scope_type.value

    statement = (
        select(BatchExportJob)
        .where(
            BatchExportJob.id
            == export_job_id,

            BatchExportJob.batch_id
            == resolved_batch_id,

            func.lower(
                func.trim(
                    BatchExportJob.college
                )
            )
            == _normalized_college_key(
                scope.college
            ),

            BatchExportJob.scope_type
            == scope_type,
        )
    )

    if scope_type == "department":
        statement = statement.where(
            BatchExportJob.department_id
            == scope.department_id
        )
    else:
        statement = statement.where(
            BatchExportJob.department_id.is_(
                None
            )
        )

    return (
        await db.execute(statement)
    ).scalar_one_or_none()



async def get_latest_batch_export_job_within_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> BatchExportJob | None:
    """
    Restore the most useful exact-scope export state.

    Priority:
    1. newest PENDING / PROCESSING job;
    2. newest fully verified COMPLETED job;
    3. newest remaining job, such as FAILED.

    This prevents a newer failed export from hiding an older
    verified export that is still eligible for download/archive.
    """

    resolved_batch_id, _ = await _resolve_export_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    conditions = [
        BatchExportJob.batch_id
        == resolved_batch_id,

        func.lower(
            func.trim(
                BatchExportJob.college
            )
        )
        == _normalized_college_key(
            scope.college
        ),

        BatchExportJob.scope_type
        == scope.scope_type.value,
    ]

    if (
        scope.scope_type.value
        == "department"
    ):
        conditions.append(
            BatchExportJob.department_id
            == scope.department_id
        )

    else:
        conditions.append(
            BatchExportJob.department_id.is_(
                None
            )
        )

    # -----------------------------------------------------
    # 1. Resume an active export first.
    # -----------------------------------------------------

    active_job = (
        await db.execute(
            select(BatchExportJob)
            .where(
                *conditions,
                BatchExportJob.status.in_(
                    [
                        "PENDING",
                        "PROCESSING",
                    ]
                ),
            )
            .order_by(
                BatchExportJob.created_at.desc(),
                BatchExportJob.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if active_job is not None:
        return active_job

    # -----------------------------------------------------
    # 2. Otherwise restore the newest genuinely usable
    #    verified export.
    # -----------------------------------------------------

    verified_job = (
        await db.execute(
            select(BatchExportJob)
            .where(
                *conditions,

                BatchExportJob.status
                == "COMPLETED",

                BatchExportJob.verified_at.is_not(
                    None
                ),

                BatchExportJob.bucket.is_not(
                    None
                ),

                BatchExportJob.object_key.is_not(
                    None
                ),

                BatchExportJob.sha256.is_not(
                    None
                ),

                BatchExportJob.file_size_bytes.is_not(
                    None
                ),
            )
            .order_by(
                BatchExportJob.verified_at.desc(),
                BatchExportJob.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if verified_job is not None:
        return verified_job

    # -----------------------------------------------------
    # 3. No active or usable verified export exists.
    #    Show the newest remaining job, normally FAILED.
    # -----------------------------------------------------

    return (
        await db.execute(
            select(BatchExportJob)
            .where(
                *conditions
            )
            .order_by(
                BatchExportJob.created_at.desc(),
                BatchExportJob.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
