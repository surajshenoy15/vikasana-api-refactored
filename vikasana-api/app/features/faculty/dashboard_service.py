from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.features.certificates.models import Certificate
from app.features.events.models import Event, EventSubmission
from app.features.faculty.models import Faculty
from app.features.faculty.permission_scope import (
    FacultyScopeType,
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.faculty.schemas.website_dashboard import (
    WebsiteFacultyDashboardStatsOut,
)
from app.features.students.models import Student


def _event_visible_conditions() -> list:
    """
    Keep website dashboard activity and certificate counts aligned with
    the existing Admin dashboard visibility behaviour.

    The checks support both the current Event model and possible
    soft-delete fields without changing the Event schema.
    """
    conditions = []

    if hasattr(Event, "is_deleted"):
        conditions.append(Event.is_deleted.is_(False))

    if hasattr(Event, "deleted_at"):
        conditions.append(Event.deleted_at.is_(None))

    if hasattr(Event, "is_active"):
        conditions.append(Event.is_active.is_(True))

    return conditions


def _apply_faculty_scope_to_statement(
    statement: Select,
    scope: WebsiteFacultyScope,
) -> Select:
    """
    Apply the authenticated website scope to Faculty aggregate queries.

    Student aggregate queries use the existing Stage 7
    apply_student_scope_to_statement helper.
    """
    college_key = scope.college.strip().casefold()

    scoped_statement = statement.where(
        func.lower(func.trim(Faculty.college)) == college_key
    )

    if scope.scope_type == FacultyScopeType.COLLEGE:
        return scoped_statement

    if scope.scope_type != FacultyScopeType.DEPARTMENT:
        raise ValueError("Unsupported website Faculty scope type")

    department_id = scope.department_id

    if (
        not isinstance(department_id, int)
        or isinstance(department_id, bool)
        or department_id <= 0
    ):
        raise ValueError(
            "Department-scoped dashboard requires a valid department_id"
        )

    return scoped_statement.where(
        Faculty.department_id == department_id
    )


async def _execute_count(
    db: AsyncSession,
    statement: Select,
) -> int:
    result = await db.execute(statement)
    return int(result.scalar() or 0)


async def get_website_faculty_dashboard_stats(
    db: AsyncSession,
    scope: WebsiteFacultyScope,
) -> WebsiteFacultyDashboardStatsOut:
    """
    Return read-only website dashboard totals within the authenticated
    College Coordinator or HOD scope.

    EventSubmission and Certificate queries join Student before the
    existing Student scope filter is applied because those tables do not
    contain college or department fields.
    """
    visible_event_conditions = _event_visible_conditions()

    total_students_statement = apply_student_scope_to_statement(
        select(func.count(Student.id)).select_from(Student),
        scope,
    )

    active_students_statement = apply_student_scope_to_statement(
        select(func.count(Student.id))
        .select_from(Student)
        .where(Student.is_active.is_(True)),
        scope,
    )

    total_faculty_statement = _apply_faculty_scope_to_statement(
        select(func.count(Faculty.id)).select_from(Faculty),
        scope,
    )

    pending_faculty_statement = _apply_faculty_scope_to_statement(
        select(func.count(Faculty.id))
        .select_from(Faculty)
        .where(Faculty.is_active.is_(False)),
        scope,
    )

    total_activities_statement = apply_student_scope_to_statement(
        select(func.count(EventSubmission.id))
        .select_from(EventSubmission)
        .join(
            Student,
            Student.id == EventSubmission.student_id,
        )
        .join(
            Event,
            Event.id == EventSubmission.event_id,
        )
        .where(*visible_event_conditions),
        scope,
    )

    approved_activities_statement = apply_student_scope_to_statement(
        select(func.count(EventSubmission.id))
        .select_from(EventSubmission)
        .join(
            Student,
            Student.id == EventSubmission.student_id,
        )
        .join(
            Event,
            Event.id == EventSubmission.event_id,
        )
        .where(
            *visible_event_conditions,
            func.lower(EventSubmission.status) == "approved",
        ),
        scope,
    )

    total_certificates_statement = apply_student_scope_to_statement(
        select(func.count(Certificate.id))
        .select_from(Certificate)
        .join(
            Student,
            Student.id == Certificate.student_id,
        )
        .join(
            Event,
            Event.id == Certificate.event_id,
        )
        .where(*visible_event_conditions),
        scope,
    )

    total_students = await _execute_count(
        db,
        total_students_statement,
    )

    active_students = await _execute_count(
        db,
        active_students_statement,
    )

    total_faculty = await _execute_count(
        db,
        total_faculty_statement,
    )

    pending_faculty = await _execute_count(
        db,
        pending_faculty_statement,
    )

    total_activities = await _execute_count(
        db,
        total_activities_statement,
    )

    approved_activities = await _execute_count(
        db,
        approved_activities_statement,
    )

    total_certificates = await _execute_count(
        db,
        total_certificates_statement,
    )

    return WebsiteFacultyDashboardStatsOut(
        totalStudents=total_students,
        activeStudents=active_students,
        totalFaculty=total_faculty,
        pendingFaculty=pending_faculty,
        totalActivities=total_activities,
        approvedActivities=approved_activities,
        totalCertificates=total_certificates,
        asOf=None,
    )
