from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.features.audit.service import append_audit_log
from app.features.faculty.models import Faculty
from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_HOD,
)
from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.faculty.schemas.website_batch_management import (
    WebsiteBatchArchivePreview,
    WebsiteBatchCreateRequest,
    WebsiteBatchCreateResponse,
    WebsiteBatchArchiveRequest,
    WebsiteBatchArchiveResponse,
    WebsiteBatchGraduateRequest,
    WebsiteBatchGraduationResponse,
    WebsiteBatchGraduationPreview,
    WebsiteBatchPromotionPreview,
    WebsiteBatchPromotionResponse,
    WebsiteBatchPromoteSelectedRequest,
    WebsiteBatchPromoteYearRequest,
    WebsiteBatchStudent,
    WebsiteBatchStudentPage,
    WebsiteBatchSummary,
    WebsiteBatchYearCount,
    WebsiteStudentMoveYearRequest,
    WebsiteStudentMoveYearResponse,
)
from app.features.organization.models import (
    AcademicBatch,
    BatchExportJob,
    Department,
    StudentAcademicHistory,
)
from app.features.students.models import Student
from app.features.students.service import (
    apply_bulk_student_assignment_update,
    apply_student_assignment_update,
)


# =========================================================
# INTERNAL HELPERS
# =========================================================


WHOLE_YEAR_PROMOTION_CHUNK_SIZE = 500
GRADUATION_CHUNK_SIZE = 500
ARCHIVE_CHUNK_SIZE = 500


def _normalized_college_key(value: str) -> str:
    return str(value or "").strip().casefold()


# =========================================================
# CREATE COLLEGE-WIDE ACADEMIC BATCH
# =========================================================


async def create_website_academic_batch(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    payload: WebsiteBatchCreateRequest,
) -> WebsiteBatchCreateResponse:
    """
    Create one college-wide AcademicBatch.

    Authorization:
        College Coordinator only.

    HODs consume the same batch but cannot create it.

    The client cannot choose:
        college
        passout_year
        name
        department

    Those values are server-derived.
    """

    if (
        str(scope.role)
        != ROLE_COLLEGE_COORDINATOR
    ):
        raise PermissionError(
            "Only the College Coordinator can create academic batches"
        )

    college = str(
        scope.college or ""
    ).strip()

    if not college:
        raise ValueError(
            "Authenticated college is missing"
        )

    admitted_year = int(
        payload.admitted_year
    )

    duration = int(
        payload.course_duration_years
    )

    passout_year = (
        admitted_year
        + duration
    )

    # Use an en dash for the human-readable academic batch label.
    name = (
        f"{admitted_year}"
        f"–"
        f"{passout_year}"
    )

    college_key = (
        _normalized_college_key(
            college
        )
    )

    # Friendly application-level duplicate check.
    existing = (
        await db.execute(
            select(
                AcademicBatch.id
            )
            .where(
                func.lower(
                    func.trim(
                        AcademicBatch.college
                    )
                )
                == college_key,

                AcademicBatch.admitted_year
                == admitted_year,

                AcademicBatch.passout_year
                == passout_year,

                AcademicBatch.course_duration_years
                == duration,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise ValueError(
            "An academic batch with the same years "
            "and course duration already exists"
        )

    batch = AcademicBatch(
        college=college,
        name=name,
        admitted_year=admitted_year,
        passout_year=passout_year,
        course_duration_years=duration,
        is_active=True,
        created_by_faculty_id=int(
            scope.faculty_id
        ),
        created_by_admin_id=None,
    )

    db.add(
        batch
    )

    try:
        # Flush first so batch.id exists for the immutable audit log.
        # The route owns the final commit.
        await db.flush()

    except IntegrityError as exc:
        await db.rollback()

        raise ValueError(
            "An academic batch with the same years "
            "and course duration already exists"
        ) from exc

    audit = await append_audit_log(
        db,
        actor_type="COLLEGE_COORDINATOR",
        actor_id=int(
            scope.faculty_id
        ),
        actor_role=ROLE_COLLEGE_COORDINATOR,
        college=college,
        department_id=None,
        action="ACADEMIC_BATCH_CREATED",
        description=(
            f"Created academic batch {name}"
        ),
        entity_type="academic_batch",
        entity_id=int(
            batch.id
        ),
        source="website_batch_management",
        metadata={
            "batch_id": int(
                batch.id
            ),
            "name": name,
            "admitted_year": admitted_year,
            "passout_year": passout_year,
            "course_duration_years": duration,
        },
        commit=False,
    )

    # Make explicit that audit creation occurred before commit.
    _ = audit

    return WebsiteBatchCreateResponse(
        batch_id=int(
            batch.id
        ),
        name=name,
        admitted_year=admitted_year,
        passout_year=passout_year,
        course_duration_years=duration,
        is_active=True,
    )


# =========================================================
# BATCH SUMMARY
# =========================================================


async def list_website_batch_summaries(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    include_inactive: bool = False,
) -> list[WebsiteBatchSummary]:
    """
    Return academic batches belonging to the authenticated website
    Faculty college with Student counts restricted to that Faculty's
    immutable authorization scope.

    College Coordinator:
        counts all Students in the authenticated college.

    HOD:
        counts only Students in the authenticated college and the
        authenticated HOD department.

    AcademicBatch itself remains college-wide.

    This function is READ ONLY:
        - no ORM objects are mutated
        - no flush
        - no commit
        - no delete
        - no bulk Student loading

    Student counts are aggregated in PostgreSQL instead of loading
    every Student row into application memory.
    """

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # LOAD COLLEGE BATCH METADATA
    # -----------------------------------------------------

    batch_statement = (
        select(AcademicBatch)
        .where(
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key
        )
    )

    if not include_inactive:
        batch_statement = (
            batch_statement.where(
                AcademicBatch.is_active.is_(
                    True
                )
            )
        )

    batch_statement = (
        batch_statement.order_by(
            AcademicBatch.admitted_year.desc(),
            AcademicBatch.passout_year.desc(),
            AcademicBatch.id.desc(),
        )
    )

    batch_result = await db.execute(
        batch_statement
    )

    batches = list(
        batch_result.scalars().all()
    )

    if not batches:
        return []

    batch_ids = [
        batch.id
        for batch in batches
    ]

    # -----------------------------------------------------
    # AGGREGATE SCOPED STUDENT COUNTS
    # -----------------------------------------------------

    student_count_statement = (
        select(
            Student.batch_id.label(
                "batch_id"
            ),
            Student.current_year.label(
                "current_year"
            ),
            Student.is_active.label(
                "is_active"
            ),
            func.count(
                Student.id
            ).label(
                "student_count"
            ),
        )
        .where(
            Student.batch_id.in_(
                batch_ids
            )
        )
    )

    # Critical authorization boundary.
    #
    # HOD:
    #   college + department_id
    #
    # College Coordinator:
    #   college
    #
    # The client does not supply either authorization value.
    student_count_statement = (
        apply_student_scope_to_statement(
            student_count_statement,
            scope,
        )
    )

    student_count_statement = (
        student_count_statement.group_by(
            Student.batch_id,
            Student.current_year,
            Student.is_active,
        )
    )

    student_count_result = (
        await db.execute(
            student_count_statement
        )
    )

    count_rows = (
        student_count_result.all()
    )

    # -----------------------------------------------------
    # BUILD SMALL IN-MEMORY AGGREGATE MAP
    # -----------------------------------------------------
    #
    # This map contains only grouped count rows:
    #
    # batch × year × active-status
    #
    # It does NOT contain Student objects.
    # -----------------------------------------------------

    aggregate: dict[int, dict] = {}

    for row in count_rows:
        batch_id = int(
            row.batch_id
        )

        student_count = int(
            row.student_count or 0
        )

        batch_data = aggregate.setdefault(
            batch_id,
            {
                "total": 0,
                "active": 0,
                "inactive": 0,
                "years": {},
            },
        )

        batch_data["total"] += (
            student_count
        )

        if bool(row.is_active):
            batch_data["active"] += (
                student_count
            )
        else:
            batch_data["inactive"] += (
                student_count
            )

        current_year = (
            row.current_year
        )

        if current_year is not None:
            year_number = int(
                current_year
            )

            batch_data["years"][
                year_number
            ] = (
                batch_data["years"].get(
                    year_number,
                    0,
                )
                + student_count
            )

    # -----------------------------------------------------
    # RESPONSE
    # -----------------------------------------------------

    response: list[
        WebsiteBatchSummary
    ] = []

    for batch in batches:
        counts = aggregate.get(
            batch.id,
            {
                "total": 0,
                "active": 0,
                "inactive": 0,
                "years": {},
            },
        )

        duration = int(
            batch.course_duration_years
        )

        year_counts = [
            WebsiteBatchYearCount(
                year=year,
                student_count=int(
                    counts["years"].get(
                        year,
                        0,
                    )
                ),
            )
            for year in range(
                1,
                duration + 1,
            )
        ]

        response.append(
            WebsiteBatchSummary(
                batch_id=batch.id,
                name=batch.name,
                admitted_year=(
                    batch.admitted_year
                ),
                passout_year=(
                    batch.passout_year
                ),
                course_duration_years=(
                    duration
                ),
                is_active=(
                    batch.is_active
                ),
                total_students=int(
                    counts["total"]
                ),
                active_students=int(
                    counts["active"]
                ),
                inactive_students=int(
                    counts["inactive"]
                ),
                year_counts=year_counts,
            )
        )

    return response

# =========================================================
# BATCH STUDENTS - READ ONLY
# =========================================================


async def list_website_batch_students(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    current_year: int | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> WebsiteBatchStudentPage:
    """
    Return one bounded page of Students for an academic batch.

    Authorization:
        College Coordinator:
            authenticated college only.

        HOD:
            authenticated college + authenticated department only.

    Pagination:
        keyset/cursor pagination using Student.id.
        No arbitrary total-result ceiling exists.
        Each request is bounded to at most 200 rows.

    Search:
        server-side search across name, USN and email.

    This function is READ ONLY.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    if limit < 1 or limit > 200:
        raise ValueError(
            "limit must be between 1 and 200"
        )

    if cursor is not None and cursor < 1:
        raise ValueError(
            "cursor must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # VERIFY BATCH BELONGS TO AUTHENTICATED COLLEGE
    # -----------------------------------------------------

    batch_statement = (
        select(AcademicBatch)
        .where(
            AcademicBatch.id == batch_id,
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key,
        )
    )

    batch_result = await db.execute(
        batch_statement
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Academic batch not found within authenticated college"
        )

    if current_year is not None:
        if (
            current_year < 1
            or current_year
            > int(batch.course_duration_years)
        ):
            raise ValueError(
                "current_year is outside this batch course duration"
            )

    # -----------------------------------------------------
    # SELECT ONLY REQUIRED DISPLAY COLUMNS
    # -----------------------------------------------------

    statement = (
        select(
            Student.id.label("id"),
            Student.name.label("name"),
            Student.usn.label("usn"),
            Student.email.label("email"),
            Student.department_id.label(
                "department_id"
            ),
            Department.name.label(
                "department_name"
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
            Student.is_active.label(
                "is_active"
            ),
        )
        .outerjoin(
            Department,
            and_(
                Department.id
                == Student.department_id,
                func.lower(
                    func.trim(
                        Department.college
                    )
                )
                == college_key,
            ),
        )
        .outerjoin(
            Faculty,
            and_(
                Faculty.id
                == Student.assigned_faculty_id,
                func.lower(
                    func.trim(
                        Faculty.college
                    )
                )
                == college_key,
                Faculty.department_id
                == Student.department_id,
            ),
        )
        .where(
            Student.batch_id == batch.id
        )
    )

    # Critical authorization boundary.
    #
    # HOD:
    #     college + department_id
    #
    # Coordinator:
    #     college
    #
    # Client cannot override these values.
    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    # -----------------------------------------------------
    # OPTIONAL FILTERS
    # -----------------------------------------------------

    if current_year is not None:
        statement = statement.where(
            Student.current_year
            == current_year
        )

    if is_active is not None:
        statement = statement.where(
            Student.is_active.is_(
                is_active
            )
        )

    if cursor is not None:
        statement = statement.where(
            Student.id > cursor
        )

    search_value = str(
        search or ""
    ).strip()

    if search_value:
        search_key = (
            search_value.casefold()
        )

        contains_pattern = (
            f"%{search_key}%"
        )

        statement = statement.where(
            or_(
                func.lower(
                    Student.name
                ).like(
                    contains_pattern
                ),
                func.lower(
                    Student.usn
                ).like(
                    contains_pattern
                ),
                func.lower(
                    func.coalesce(
                        Student.email,
                        "",
                    )
                ).like(
                    contains_pattern
                ),
            )
        )

    # Fetch one extra row only to determine has_more.
    statement = (
        statement
        .order_by(
            Student.id.asc()
        )
        .limit(
            limit + 1
        )
    )

    result = await db.execute(
        statement
    )

    rows = list(
        result.mappings().all()
    )

    has_more = len(rows) > limit

    page_rows = rows[:limit]

    items = [
        WebsiteBatchStudent(
            id=int(row["id"]),
            name=row["name"],
            usn=row["usn"],
            email=row["email"],
            department_id=row[
                "department_id"
            ],
            department_name=row[
                "department_name"
            ],
            batch_id=int(
                row["batch_id"]
            ),
            batch_name=batch.name,
            current_year=row[
                "current_year"
            ],
            assigned_faculty_id=row[
                "assigned_faculty_id"
            ],
            assigned_faculty_name=row[
                "assigned_faculty_name"
            ],
            is_active=bool(
                row["is_active"]
            ),
        )
        for row in page_rows
    ]

    next_cursor = None

    if has_more and items:
        next_cursor = items[-1].id

    return WebsiteBatchStudentPage(
        items=items,
        returned_count=len(items),
        limit=limit,
        next_cursor=next_cursor,
        has_more=has_more,
    )

# =========================================================
# MANUAL MOVE YEAR
# =========================================================


async def move_website_student_year(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    payload: WebsiteStudentMoveYearRequest,
) -> WebsiteStudentMoveYearResponse:
    """
    Move exactly one Student within the authenticated Website
    Faculty scope to another academic year.

    Lookup:
        exact case-insensitive USN OR email.

    Authorization:
        College Coordinator:
            authenticated college only.

        HOD:
            authenticated college + authenticated department only.

    Only current_year is changed.

    These values are deliberately preserved:
        college
        department_id
        batch_id
        assigned_faculty_id

    The existing student assignment engine adds the corresponding
    StudentAcademicHistory record.

    This service does NOT commit. The route controls the transaction.
    """

    # -----------------------------------------------------
    # BUILD EXACT SCOPED LOOKUP
    # -----------------------------------------------------

    statement = select(Student)

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    if payload.usn is not None:
        usn_key = str(
            payload.usn
        ).strip().casefold()

        statement = statement.where(
            func.lower(
                func.trim(
                    Student.usn
                )
            )
            == usn_key
        )

    else:
        email_key = str(
            payload.email
        ).strip().casefold()

        statement = statement.where(
            func.lower(
                func.trim(
                    Student.email
                )
            )
            == email_key
        )

    # Read at most two rows so malformed/legacy duplicate identity
    # data cannot silently select an arbitrary Student.
    statement = (
        statement
        .order_by(
            Student.id.asc()
        )
        .limit(2)
    )

    result = await db.execute(
        statement
    )

    students = list(
        result.scalars().all()
    )

    if not students:
        # Do not reveal whether the identifier exists outside the
        # authenticated Faculty scope.
        raise ValueError(
            "Student not found within authenticated scope"
        )

    if len(students) > 1:
        raise ValueError(
            "Student identifier is ambiguous within authenticated scope"
        )

    student = students[0]

    # -----------------------------------------------------
    # REQUIRE EXISTING ACADEMIC PLACEMENT
    # -----------------------------------------------------

    if student.department_id is None:
        raise ValueError(
            "Student has no department assignment"
        )

    if student.batch_id is None:
        raise ValueError(
            "Student has no academic batch assignment"
        )

    if student.current_year is None:
        raise ValueError(
            "Student has no current academic year"
        )

    from_year = int(
        student.current_year
    )

    to_year = int(
        payload.to_year
    )

    if from_year == to_year:
        raise ValueError(
            "Student is already in the requested academic year"
        )

    # -----------------------------------------------------
    # SNAPSHOT IMMUTABLE VALUES FOR SAFETY CHECK
    # -----------------------------------------------------

    original_college = student.college
    original_department_id = (
        student.department_id
    )
    original_batch_id = (
        student.batch_id
    )
    original_assigned_faculty_id = (
        student.assigned_faculty_id
    )

    reason = str(
        payload.reason
    ).strip()

    # -----------------------------------------------------
    # REUSE EXISTING VALIDATION + HISTORY ENGINE
    # -----------------------------------------------------

    await apply_student_assignment_update(
        db=db,
        student=student,
        assignment_data={
            "current_year": to_year,
        },
        changed_by_faculty_id=scope.faculty_id,
        reason=reason,
    )

    # -----------------------------------------------------
    # DEFENSIVE INVARIANT CHECK
    # -----------------------------------------------------

    if student.college != original_college:
        raise RuntimeError(
            "Manual Move Year unexpectedly changed student college"
        )

    if student.department_id != original_department_id:
        raise RuntimeError(
            "Manual Move Year unexpectedly changed department"
        )

    if student.batch_id != original_batch_id:
        raise RuntimeError(
            "Manual Move Year unexpectedly changed academic batch"
        )

    if (
        student.assigned_faculty_id
        != original_assigned_faculty_id
    ):
        raise RuntimeError(
            "Manual Move Year unexpectedly changed assigned Faculty"
        )

    if int(student.current_year) != to_year:
        raise RuntimeError(
            "Manual Move Year failed to update current_year"
        )

    return WebsiteStudentMoveYearResponse(
        student_id=int(
            student.id
        ),
        name=student.name,
        usn=student.usn,
        email=student.email,
        department_id=student.department_id,
        batch_id=int(
            student.batch_id
        ),
        from_year=from_year,
        to_year=to_year,
        reason=reason,
        history_created=True,
    )

# =========================================================
# PROMOTE SELECTED STUDENTS
# =========================================================


async def promote_selected_website_batch_students(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    payload: WebsiteBatchPromoteSelectedRequest,
) -> WebsiteBatchPromotionResponse:
    """
    Promote selected active Students exactly one academic year
    forward inside the authenticated Website Faculty scope.

    Per-request student_ids are bounded by the request schema.

    Student IDs that are:
        outside authenticated scope
        missing
        in another batch
        in another current_year

    are treated as skipped without revealing which security boundary
    caused the Student to be unavailable.

    Inactive Students in the requested batch/from_year are counted
    separately and are never promoted.

    Only current_year is changed.

    This service does NOT commit.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # VERIFY ACTIVE BATCH + COURSE DURATION
    # -----------------------------------------------------

    batch_result = await db.execute(
        select(AcademicBatch).where(
            AcademicBatch.id == batch_id,
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key,
            AcademicBatch.is_active.is_(True),
        )
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    from_year = int(
        payload.from_year
    )

    to_year = int(
        payload.to_year
    )

    duration = int(
        batch.course_duration_years
    )

    if from_year > duration:
        raise ValueError(
            "from_year exceeds the academic batch course duration"
        )

    if to_year > duration:
        raise ValueError(
            "to_year exceeds the academic batch course duration"
        )

    # The schema already validates this, but retain a service-level
    # invariant so callers cannot bypass the Pydantic contract.
    if to_year != from_year + 1:
        raise ValueError(
            "Batch promotion must move students exactly one academic year forward"
        )

    selected_ids = list(
        payload.student_ids
    )

    selected_count = len(
        selected_ids
    )

    # -----------------------------------------------------
    # LOAD ONLY STUDENTS VISIBLE TO AUTHENTICATED SCOPE
    # -----------------------------------------------------

    statement = select(Student).where(
        Student.id.in_(
            selected_ids
        )
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    statement = statement.order_by(
        Student.id.asc()
    )

    result = await db.execute(
        statement
    )

    scoped_students = list(
        result.scalars().all()
    )

    # -----------------------------------------------------
    # CLASSIFY SELECTED STUDENTS
    # -----------------------------------------------------

    eligible_students: list[Student] = []

    inactive_count = 0

    for student in scoped_students:
        # Wrong batch or stale/wrong year selection.
        if (
            student.batch_id != batch_id
            or student.current_year != from_year
        ):
            continue

        if not bool(student.is_active):
            inactive_count += 1
            continue

        eligible_students.append(
            student
        )

    eligible_count = len(
        eligible_students
    )

    # Anything not promoted is skipped, including:
    #   inactive
    #   out of scope
    #   missing IDs
    #   wrong batch
    #   wrong/stale year
    skipped_count = (
        selected_count
        - eligible_count
    )

    # -----------------------------------------------------
    # PROMOTE ALL ELIGIBLE STUDENTS ATOMICALLY
    # -----------------------------------------------------

    if eligible_students:
        await apply_bulk_student_assignment_update(
            db=db,
            students=eligible_students,
            assignment_data={
                "current_year": to_year,
            },
            changed_by_faculty_id=scope.faculty_id,
            reason=str(
                payload.reason
            ).strip(),
        )

    # -----------------------------------------------------
    # DEFENSIVE INVARIANT CHECK
    # -----------------------------------------------------

    for student in eligible_students:
        if student.batch_id != batch_id:
            raise RuntimeError(
                "Selected promotion unexpectedly changed academic batch"
            )

        if student.current_year != to_year:
            raise RuntimeError(
                "Selected promotion failed to update current_year"
            )

    promoted_student_ids = [
        int(student.id)
        for student in eligible_students
    ]

    return WebsiteBatchPromotionResponse(
        batch_id=int(
            batch.id
        ),
        from_year=from_year,
        to_year=to_year,
        selected_count=selected_count,
        eligible_count=eligible_count,
        promoted_count=eligible_count,
        inactive_count=inactive_count,
        skipped_count=skipped_count,
        promoted_student_ids=(
            promoted_student_ids
        ),
    )

# =========================================================
# PROMOTION PREVIEW
# =========================================================


async def preview_website_batch_year_promotion(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    from_year: int,
) -> WebsiteBatchPromotionPreview:
    """
    Return exact scoped counts for an entire-year promotion
    confirmation.

    This function is read-only.

    selected_count:
        all scoped Students in batch/from_year.

    eligible_count:
        active scoped Students in batch/from_year.

    inactive_count:
        inactive scoped Students in batch/from_year.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    batch_result = await db.execute(
        select(AcademicBatch).where(
            AcademicBatch.id == batch_id,
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key,
            AcademicBatch.is_active.is_(True),
        )
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    normalized_year = int(
        from_year
    )

    duration = int(
        batch.course_duration_years
    )

    if (
        normalized_year < 1
        or normalized_year > duration
    ):
        raise ValueError(
            "from_year exceeds the academic batch course duration"
        )

    if normalized_year >= duration:
        raise ValueError(
            "Final academic year cannot be promoted further"
        )

    statement = (
        select(
            Student.is_active,
            func.count(
                Student.id
            ),
        )
        .where(
            Student.batch_id == batch_id,
            Student.current_year == normalized_year,
        )
        .group_by(
            Student.is_active
        )
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    result = await db.execute(
        statement
    )

    eligible_count = 0
    inactive_count = 0

    for (
        is_active,
        count,
    ) in result.all():
        value = int(
            count
            or 0
        )

        if bool(is_active):
            eligible_count = value
        else:
            inactive_count = value

    return WebsiteBatchPromotionPreview(
        batch_id=int(
            batch.id
        ),
        from_year=normalized_year,
        to_year=normalized_year + 1,
        selected_count=(
            eligible_count
            + inactive_count
        ),
        eligible_count=eligible_count,
        inactive_count=inactive_count,
    )


# =========================================================
# PROMOTE ENTIRE ACADEMIC YEAR
# =========================================================


async def promote_entire_website_batch_year(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    payload: WebsiteBatchPromoteYearRequest,
) -> WebsiteBatchPromotionResponse:
    """
    Promote every eligible active Student in one batch/year inside
    the authenticated Website Faculty scope.

    The frontend does not provide Student IDs.

    Scalability:
        - no arbitrary total-result ceiling
        - bounded Student chunks
        - only lightweight Student columns are loaded
        - history rows are inserted in bulk per chunk
        - no per-Student validation queries

    Authorization:
        - College Coordinator: authenticated college
        - HOD: authenticated college + exact department

    Only Student.current_year changes.

    This service deliberately does NOT commit. The API route owns
    the transaction boundary.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # VERIFY ACTIVE COLLEGE-WIDE BATCH ONCE
    # -----------------------------------------------------

    batch_result = await db.execute(
        select(AcademicBatch).where(
            AcademicBatch.id == batch_id,
            func.lower(
                func.trim(
                    AcademicBatch.college
                )
            )
            == college_key,
            AcademicBatch.is_active.is_(True),
        )
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    from_year = int(
        payload.from_year
    )

    to_year = int(
        payload.to_year
    )

    duration = int(
        batch.course_duration_years
    )

    if from_year > duration:
        raise ValueError(
            "from_year exceeds the academic batch course duration"
        )

    if to_year > duration:
        raise ValueError(
            "to_year exceeds the academic batch course duration"
        )

    # Keep the invariant in the service even if a caller bypasses
    # the Pydantic request model.
    if to_year != from_year + 1:
        raise ValueError(
            "Batch promotion must move students exactly one academic year forward"
        )

    reason = str(
        payload.reason
        or ""
    ).strip()

    if not reason:
        reason = "Academic year promotion"

    # -----------------------------------------------------
    # COUNT THE SCOPED YEAR BEFORE MUTATING IT
    # -----------------------------------------------------
    #
    # selected_count for an entire-year operation means:
    # every scoped Student currently in batch/from_year,
    # including inactive Students.
    #
    # eligible_count = active Students
    # inactive_count = inactive Students
    # skipped_count  = inactive Students
    # -----------------------------------------------------

    count_statement = (
        select(
            Student.is_active,
            func.count(
                Student.id
            ),
        )
        .where(
            Student.batch_id == batch_id,
            Student.current_year == from_year,
        )
        .group_by(
            Student.is_active
        )
    )

    count_statement = (
        apply_student_scope_to_statement(
            count_statement,
            scope,
        )
    )

    count_result = await db.execute(
        count_statement
    )

    active_count = 0
    inactive_count = 0

    for (
        is_active,
        count,
    ) in count_result.all():
        if bool(is_active):
            active_count = int(
                count
                or 0
            )
        else:
            inactive_count = int(
                count
                or 0
            )

    selected_count = (
        active_count
        + inactive_count
    )

    eligible_count = active_count

    # -----------------------------------------------------
    # CHUNKED PROMOTION
    # -----------------------------------------------------
    #
    # Rows are selected in Student.id order and locked before their
    # current_year is changed. The cursor advances monotonically.
    #
    # We intentionally do not return every promoted Student ID for
    # an entire-year operation; doing so would create an unbounded
    # API response for large institutions.
    # -----------------------------------------------------

    promoted_count = 0
    cursor = 0

    while True:
        chunk_statement = (
            select(
                Student.id,
                Student.department_id,
                Student.batch_id,
            )
            .where(
                Student.batch_id == batch_id,
                Student.current_year == from_year,
                Student.is_active.is_(True),
                Student.id > cursor,
            )
            .order_by(
                Student.id.asc()
            )
            .limit(
                WHOLE_YEAR_PROMOTION_CHUNK_SIZE
            )
            .with_for_update(
                of=Student
            )
        )

        chunk_statement = (
            apply_student_scope_to_statement(
                chunk_statement,
                scope,
            )
        )

        chunk_result = await db.execute(
            chunk_statement
        )

        chunk_rows = list(
            chunk_result.all()
        )

        if not chunk_rows:
            break

        student_ids = [
            int(row.id)
            for row in chunk_rows
        ]

        cursor = student_ids[-1]

        # -------------------------------------------------
        # UPDATE ONLY THE LOCKED ELIGIBLE ROWS
        # -------------------------------------------------

        update_result = await db.execute(
            update(Student)
            .where(
                Student.id.in_(
                    student_ids
                ),
                Student.batch_id == batch_id,
                Student.current_year == from_year,
                Student.is_active.is_(True),
            )
            .values(
                current_year=to_year
            )
            .returning(
                Student.id
            )
        )

        updated_ids = {
            int(student_id)
            for student_id
            in update_result.scalars().all()
        }

        expected_ids = set(
            student_ids
        )

        if updated_ids != expected_ids:
            raise RuntimeError(
                "Entire-year promotion encountered a concurrent or inconsistent Student update"
            )

        # -------------------------------------------------
        # BULK ACADEMIC HISTORY INSERT
        # -------------------------------------------------

        history_rows = [
            {
                "student_id": int(
                    row.id
                ),
                "action": "ACADEMIC_UPDATED",
                "from_department_id": (
                    row.department_id
                ),
                "to_department_id": (
                    row.department_id
                ),
                "from_batch_id": int(
                    row.batch_id
                ),
                "to_batch_id": int(
                    row.batch_id
                ),
                "from_year": from_year,
                "to_year": to_year,
                "reason": reason,
                "changed_by_faculty_id": (
                    scope.faculty_id
                ),
                "changed_by_admin_id": None,
            }
            for row in chunk_rows
        ]

        await db.execute(
            insert(
                StudentAcademicHistory
            ),
            history_rows,
        )

        promoted_count += len(
            student_ids
        )

    # -----------------------------------------------------
    # FINAL DEFENSIVE COUNT CHECK
    # -----------------------------------------------------

    if promoted_count != eligible_count:
        raise RuntimeError(
            "Entire-year promotion count changed during processing"
        )

    return WebsiteBatchPromotionResponse(
        batch_id=int(
            batch.id
        ),
        from_year=from_year,
        to_year=to_year,
        selected_count=selected_count,
        eligible_count=eligible_count,
        promoted_count=promoted_count,
        inactive_count=inactive_count,
        skipped_count=inactive_count,
        # Deliberately bounded response:
        # whole-year operations may contain extremely many Students.
        promoted_student_ids=[],
    )


# =========================================================
# FINAL-YEAR GRADUATION PREVIEW - READ ONLY
# =========================================================


async def preview_website_batch_graduation(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> WebsiteBatchGraduationPreview:
    """
    Return scoped final-year graduation eligibility counts.

    College Coordinator:
        authenticated college + exact batch.

    HOD:
        authenticated college + authenticated department
        + exact batch.

    Graduation eligibility:

        Student.current_year == batch.course_duration_years
        Student.lifecycle_status == ACTIVE
        Student.is_active == True

    Students with legacy is_active=False are not automatically
    graduated.

    Students already GRADUATED / ARCHIVED / PURGED are counted
    separately and are not eligible again.

    This function is strictly READ ONLY:
        - no Student update
        - no audit insert
        - no flush
        - no commit
        - no delete
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    batch_statement = (
        select(AcademicBatch)
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

    batch_result = await db.execute(
        batch_statement
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    final_year = int(
        batch.course_duration_years
    )

    if final_year < 1 or final_year > 8:
        raise ValueError(
            "Academic batch has invalid course duration"
        )

    statement = (
        select(
            Student.lifecycle_status,
            Student.is_active,
            func.count(
                Student.id
            ),
        )
        .where(
            Student.batch_id == batch.id,
            Student.current_year == final_year,
        )
        .group_by(
            Student.lifecycle_status,
            Student.is_active,
        )
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    result = await db.execute(
        statement
    )

    selected_count = 0
    eligible_count = 0
    inactive_count = 0
    already_graduated_count = 0
    archived_count = 0
    purged_count = 0

    for (
        lifecycle_status,
        is_active,
        count,
    ) in result.all():
        normalized_lifecycle = (
            str(
                lifecycle_status
                or "ACTIVE"
            )
            .strip()
            .upper()
        )

        row_count = int(
            count
            or 0
        )

        selected_count += row_count

        if normalized_lifecycle == "ACTIVE":
            if bool(is_active):
                eligible_count += row_count
            else:
                inactive_count += row_count

        elif normalized_lifecycle == "GRADUATED":
            already_graduated_count += row_count

        elif normalized_lifecycle == "ARCHIVED":
            archived_count += row_count

        elif normalized_lifecycle == "PURGED":
            purged_count += row_count

        else:
            raise RuntimeError(
                "Unexpected Student lifecycle status "
                f"{normalized_lifecycle!r}"
            )

    skipped_count = (
        selected_count
        - eligible_count
    )

    return WebsiteBatchGraduationPreview(
        batch_id=batch.id,
        final_year=final_year,
        selected_count=selected_count,
        eligible_count=eligible_count,
        inactive_count=inactive_count,
        already_graduated_count=already_graduated_count,
        archived_count=archived_count,
        purged_count=purged_count,
        skipped_count=skipped_count,
    )


# =========================================================
# FINAL-YEAR GRADUATION
# =========================================================


async def graduate_website_batch_final_year(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    payload: WebsiteBatchGraduateRequest,
    request: Request | None = None,
) -> WebsiteBatchGraduationResponse:
    """
    Graduate eligible final-year Students within the immutable
    authenticated website Faculty scope.

    Eligibility:
        exact authenticated college
        exact authenticated HOD department when department-scoped
        exact academic batch
        current_year == batch.course_duration_years
        lifecycle_status == ACTIVE
        legacy is_active == True

    Transition:
        ACTIVE -> GRADUATED

    Only these Student columns are changed:
        lifecycle_status
        graduated_at

    Academic assignment, points, certificates and legacy is_active
    remain unchanged.

    Scalability:
        eligible Student IDs are selected and updated in bounded
        keyset chunks.

    Audit:
        one immutable STUDENTS_GRADUATED operation-level AuditLog
        is appended with commit=False so the route can commit the
        Student transition and audit row atomically.

    This service does NOT commit.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    reason = str(
        payload.reason
        or ""
    ).strip()

    if len(reason) < 2:
        raise ValueError(
            "Graduation reason is required"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # VERIFY BATCH
    # -----------------------------------------------------

    batch_statement = (
        select(AcademicBatch)
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

    batch_result = await db.execute(
        batch_statement
    )

    batch = batch_result.scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    final_year = int(
        batch.course_duration_years
    )

    if final_year < 1 or final_year > 8:
        raise ValueError(
            "Academic batch has invalid course duration"
        )

    # -----------------------------------------------------
    # PRE-TRANSITION COUNTS
    # -----------------------------------------------------

    preview = await preview_website_batch_graduation(
        db=db,
        scope=scope,
        batch_id=batch.id,
    )

    # -----------------------------------------------------
    # RESOLVE AUTHENTICATED ACTOR SNAPSHOT
    # -----------------------------------------------------

    actor_statement = (
        select(Faculty)
        .where(
            Faculty.id == scope.faculty_id,
            func.lower(
                func.trim(
                    Faculty.college
                )
            )
            == college_key,
        )
    )

    actor_result = await db.execute(
        actor_statement
    )

    actor = actor_result.scalar_one_or_none()

    if actor is None:
        raise RuntimeError(
            "Authenticated Faculty actor could not be resolved"
        )

    if scope.role == ROLE_COLLEGE_COORDINATOR:
        actor_type = "COLLEGE_COORDINATOR"

    elif scope.role == ROLE_HOD:
        actor_type = "HOD"

    else:
        raise ValueError(
            "Unsupported website Faculty role for graduation"
        )

    department_name = None

    if scope.department_id is not None:
        department_statement = (
            select(Department.name)
            .where(
                Department.id
                == scope.department_id,
                func.lower(
                    func.trim(
                        Department.college
                    )
                )
                == college_key,
            )
        )

        department_result = await db.execute(
            department_statement
        )

        department_name = (
            department_result.scalar_one_or_none()
        )

        if department_name is None:
            raise RuntimeError(
                "Authenticated Faculty department could not be resolved"
            )

    # -----------------------------------------------------
    # NO-OP / IDEMPOTENT CASE
    # -----------------------------------------------------

    if preview.eligible_count == 0:
        return WebsiteBatchGraduationResponse(
            batch_id=batch.id,
            final_year=final_year,
            selected_count=preview.selected_count,
            eligible_count=0,
            graduated_count=0,
            inactive_count=preview.inactive_count,
            already_graduated_count=(
                preview.already_graduated_count
            ),
            archived_count=preview.archived_count,
            purged_count=preview.purged_count,
            skipped_count=preview.selected_count,
            audit_log_id=None,
        )

    # -----------------------------------------------------
    # CHUNKED ACTIVE -> GRADUATED TRANSITION
    # -----------------------------------------------------

    graduated_at = datetime.now(
        timezone.utc
    )

    cursor = 0
    graduated_count = 0

    while True:
        chunk_statement = (
            select(
                Student.id
            )
            .where(
                Student.batch_id == batch.id,
                Student.current_year == final_year,
                Student.lifecycle_status == "ACTIVE",
                Student.is_active.is_(True),
                Student.id > cursor,
            )
            .order_by(
                Student.id.asc()
            )
            .limit(
                GRADUATION_CHUNK_SIZE
            )
            .with_for_update(
                of=Student
            )
        )

        chunk_statement = apply_student_scope_to_statement(
            chunk_statement,
            scope,
        )

        chunk_result = await db.execute(
            chunk_statement
        )

        student_ids = [
            int(row[0])
            for row in chunk_result.all()
        ]

        if not student_ids:
            break

        update_statement = (
            update(Student)
            .where(
                Student.id.in_(
                    student_ids
                ),
                Student.batch_id == batch.id,
                Student.current_year == final_year,
                Student.lifecycle_status == "ACTIVE",
                Student.is_active.is_(True),
            )
            .values(
                lifecycle_status="GRADUATED",
                graduated_at=graduated_at,
            )
            .returning(
                Student.id
            )
        )

        update_result = await db.execute(
            update_statement
        )

        updated_ids = [
            int(row[0])
            for row in update_result.all()
        ]

        if set(updated_ids) != set(student_ids):
            raise RuntimeError(
                "Graduation update did not match the locked Student chunk"
            )

        graduated_count += len(
            updated_ids
        )

        cursor = student_ids[-1]

    # A mismatch can only occur if the eligibility set changed
    # concurrently between preview and the locked transition.
    # Roll back instead of silently returning inconsistent counts.
    if graduated_count != preview.eligible_count:
        raise RuntimeError(
            "Graduation eligibility changed during processing; retry"
        )

    # -----------------------------------------------------
    # IMMUTABLE OPERATION AUDIT
    # -----------------------------------------------------

    audit_row = await append_audit_log(
        db,
        actor_type=actor_type,
        actor_id=actor.id,
        actor_role=scope.role,
        actor_name=actor.full_name,
        actor_identifier=str(
            actor.id
        ),
        actor_email=actor.email,
        college=scope.college,
        department_id=scope.department_id,
        department_name=department_name,
        action="STUDENTS_GRADUATED",
        description=(
            f"{actor.full_name} graduated "
            f"{graduated_count} final-year student(s) "
            f"from academic batch {batch.name}."
        ),
        entity_type="academic_batch",
        entity_id=batch.id,
        source="website_portal",
        request=request,
        metadata={
            "batch_id": batch.id,
            "batch_name": batch.name,
            "final_year": final_year,
            "scope_type": scope.scope_type.value,
            "department_id": scope.department_id,
            "transition": "ACTIVE_TO_GRADUATED",
            "selected_count": preview.selected_count,
            "eligible_count": preview.eligible_count,
            "graduated_count": graduated_count,
            "inactive_count": preview.inactive_count,
            "already_graduated_count": (
                preview.already_graduated_count
            ),
            "archived_count": preview.archived_count,
            "purged_count": preview.purged_count,
            "reason": reason,
        },
        commit=False,
    )

    skipped_count = (
        preview.selected_count
        - graduated_count
    )

    return WebsiteBatchGraduationResponse(
        batch_id=batch.id,
        final_year=final_year,
        selected_count=preview.selected_count,
        eligible_count=preview.eligible_count,
        graduated_count=graduated_count,
        inactive_count=preview.inactive_count,
        already_graduated_count=(
            preview.already_graduated_count
        ),
        archived_count=preview.archived_count,
        purged_count=preview.purged_count,
        skipped_count=skipped_count,
        audit_log_id=audit_row.id,
    )


# =========================================================
# BATCH ARCHIVE PREVIEW
# =========================================================


async def preview_website_batch_archive(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> WebsiteBatchArchivePreview:
    """
    Return the lifecycle distribution for one academic batch inside
    the immutable authenticated Website Faculty scope.

    READ ONLY.

    Archive eligibility is lifecycle-based:
        GRADUATED -> eligible for ARCHIVED

    Student.is_active is intentionally NOT an eligibility condition.
    It remains the separate legacy/account activation flag.

    No Student rows, audit rows, certificates, activities or event
    records are modified.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    batch_statement = (
        select(AcademicBatch.id)
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

    batch_result = await db.execute(
        batch_statement
    )

    resolved_batch_id = (
        batch_result.scalar_one_or_none()
    )

    if resolved_batch_id is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    statement = (
        select(
            Student.lifecycle_status,
            Student.is_active,
            func.count(
                Student.id
            ),
        )
        .where(
            Student.batch_id
            == resolved_batch_id
        )
        .group_by(
            Student.lifecycle_status,
            Student.is_active,
        )
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    result = await db.execute(
        statement
    )

    selected_count = 0

    active_count = 0
    graduated_count = 0
    archived_count = 0
    purged_count = 0

    legacy_inactive_eligible_count = 0

    for (
        lifecycle_status,
        is_active,
        count,
    ) in result.all():
        row_count = int(
            count
            or 0
        )

        selected_count += row_count

        normalized_lifecycle = str(
            lifecycle_status
            or "ACTIVE"
        ).strip().upper()

        if normalized_lifecycle == "ACTIVE":
            active_count += row_count

        elif normalized_lifecycle == "GRADUATED":
            graduated_count += row_count

            if not bool(
                is_active
            ):
                legacy_inactive_eligible_count += (
                    row_count
                )

        elif normalized_lifecycle == "ARCHIVED":
            archived_count += row_count

        elif normalized_lifecycle == "PURGED":
            purged_count += row_count

        else:
            raise RuntimeError(
                "Unexpected Student lifecycle status "
                f"during archive preview: "
                f"{normalized_lifecycle!r}"
            )

    eligible_count = graduated_count

    skipped_count = (
        selected_count
        - eligible_count
    )

    return WebsiteBatchArchivePreview(
        batch_id=int(
            resolved_batch_id
        ),
        selected_count=selected_count,
        eligible_count=eligible_count,
        active_count=active_count,
        graduated_count=graduated_count,
        archived_count=archived_count,
        purged_count=purged_count,
        legacy_inactive_eligible_count=(
            legacy_inactive_eligible_count
        ),
        skipped_count=skipped_count,
    )



# =========================================================
# VERIFIED FINAL ARCHIVE
# =========================================================


async def archive_website_batch_graduated_students(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    payload: WebsiteBatchArchiveRequest,
    request: Request | None = None,
) -> WebsiteBatchArchiveResponse:
    """
    Transition scoped Students:

        GRADUATED -> ARCHIVED

    Safety gate:
        A COMPLETED + verified exact-scope BatchExportJob must exist.

    Additional stale-export guards:
        - current scoped Student count must equal students.csv row count
        - no currently GRADUATED Student may have graduated after the
          verified export started
        - no GRADUATED Student may have NULL graduated_at

    Student.is_active is deliberately NOT changed and is not an
    archive eligibility condition.

    No activity, event, certificate, history or Student rows are
    deleted.

    This service does NOT commit.
    """

    if batch_id < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    reason = str(
        payload.reason
        or ""
    ).strip()

    if len(reason) < 2:
        raise ValueError(
            "Archive reason is required"
        )

    college_key = _normalized_college_key(
        scope.college
    )

    # -----------------------------------------------------
    # VERIFY ACTIVE BATCH
    # -----------------------------------------------------

    batch = (
        await db.execute(
            select(AcademicBatch)
            .where(
                AcademicBatch.id == batch_id,
                func.lower(
                    func.trim(
                        AcademicBatch.college
                    )
                )
                == college_key,
                AcademicBatch.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found within authenticated college"
        )

    # -----------------------------------------------------
    # CURRENT LIFECYCLE DISTRIBUTION
    # -----------------------------------------------------

    preview = await preview_website_batch_archive(
        db=db,
        scope=scope,
        batch_id=batch.id,
    )

    # -----------------------------------------------------
    # EXACT-SCOPE VERIFIED EXPORT GATE
    # -----------------------------------------------------

    export_statement = (
        select(BatchExportJob)
        .where(
            BatchExportJob.batch_id == batch.id,

            func.lower(
                func.trim(
                    BatchExportJob.college
                )
            )
            == college_key,

            BatchExportJob.scope_type
            == scope.scope_type.value,

            BatchExportJob.status
            == "COMPLETED",

            BatchExportJob.verified_at.is_not(
                None
            ),

            BatchExportJob.started_at.is_not(
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

    if scope.scope_type.value == "department":
        export_statement = (
            export_statement.where(
                BatchExportJob.department_id
                == scope.department_id
            )
        )
    else:
        export_statement = (
            export_statement.where(
                BatchExportJob.department_id.is_(
                    None
                )
            )
        )

    export_job = (
        await db.execute(
            export_statement
        )
    ).scalar_one_or_none()

    if export_job is None:
        raise RuntimeError(
            "A completed verified final export is required before archive"
        )

    # students.csv must still represent the complete scoped
    # Student population.
    if int(
        export_job.students_rows
        or 0
    ) != int(
        preview.selected_count
    ):
        raise RuntimeError(
            "Student set changed after verified export; generate a new export"
        )

    # Any newly-graduated Student would not be safely covered by the
    # export snapshot used as this archive's evidence.
    stale_statement = (
        select(Student.id)
        .where(
            Student.batch_id == batch.id,
            Student.lifecycle_status == "GRADUATED",
            or_(
                Student.graduated_at.is_(
                    None
                ),
                Student.graduated_at
                > export_job.started_at,
            ),
        )
        .order_by(
            Student.id.asc()
        )
        .limit(1)
    )

    stale_statement = apply_student_scope_to_statement(
        stale_statement,
        scope,
    )

    stale_student_id = (
        await db.execute(
            stale_statement
        )
    ).scalar_one_or_none()

    if stale_student_id is not None:
        raise RuntimeError(
            "Graduated student set changed after verified export; "
            "generate a new export"
        )

    # -----------------------------------------------------
    # RESOLVE ACTOR
    # -----------------------------------------------------

    actor = (
        await db.execute(
            select(Faculty)
            .where(
                Faculty.id
                == scope.faculty_id,

                func.lower(
                    func.trim(
                        Faculty.college
                    )
                )
                == college_key,
            )
        )
    ).scalar_one_or_none()

    if actor is None:
        raise RuntimeError(
            "Authenticated Faculty actor could not be resolved"
        )

    if scope.role == ROLE_COLLEGE_COORDINATOR:
        actor_type = "COLLEGE_COORDINATOR"

    elif scope.role == ROLE_HOD:
        actor_type = "HOD"

    else:
        raise ValueError(
            "Unsupported website Faculty role for archive"
        )

    department_name = None

    if scope.department_id is not None:
        department_name = (
            await db.execute(
                select(Department.name)
                .where(
                    Department.id
                    == scope.department_id,

                    func.lower(
                        func.trim(
                            Department.college
                        )
                    )
                    == college_key,
                )
            )
        ).scalar_one_or_none()

        if department_name is None:
            raise RuntimeError(
                "Authenticated Faculty department could not be resolved"
            )

    # -----------------------------------------------------
    # IDEMPOTENT NO-OP
    # -----------------------------------------------------

    if preview.eligible_count == 0:
        return WebsiteBatchArchiveResponse(
            batch_id=batch.id,
            export_job_id=export_job.id,
            selected_count=preview.selected_count,
            eligible_count=0,
            archived_count=0,
            active_count=preview.active_count,
            already_archived_count=preview.archived_count,
            purged_count=preview.purged_count,
            legacy_inactive_eligible_count=0,
            skipped_count=preview.selected_count,
            audit_log_id=None,
        )

    # -----------------------------------------------------
    # CHUNKED GRADUATED -> ARCHIVED
    # -----------------------------------------------------

    archived_at = datetime.now(
        timezone.utc
    )

    cursor = 0
    archived_count = 0

    while True:
        chunk_statement = (
            select(Student.id)
            .where(
                Student.batch_id == batch.id,
                Student.lifecycle_status
                == "GRADUATED",
                Student.id > cursor,
            )
            .order_by(
                Student.id.asc()
            )
            .limit(
                ARCHIVE_CHUNK_SIZE
            )
            .with_for_update(
                of=Student
            )
        )

        chunk_statement = apply_student_scope_to_statement(
            chunk_statement,
            scope,
        )

        student_ids = [
            int(row[0])
            for row in (
                await db.execute(
                    chunk_statement
                )
            ).all()
        ]

        if not student_ids:
            break

        updated_ids = [
            int(row[0])
            for row in (
                await db.execute(
                    update(Student)
                    .where(
                        Student.id.in_(
                            student_ids
                        ),
                        Student.batch_id
                        == batch.id,
                        Student.lifecycle_status
                        == "GRADUATED",
                    )
                    .values(
                        lifecycle_status="ARCHIVED",
                        archived_at=archived_at,
                    )
                    .returning(
                        Student.id
                    )
                )
            ).all()
        ]

        if set(updated_ids) != set(
            student_ids
        ):
            raise RuntimeError(
                "Archive update did not match the locked Student chunk"
            )

        archived_count += len(
            updated_ids
        )

        cursor = student_ids[-1]

    if archived_count != preview.eligible_count:
        raise RuntimeError(
            "Archive eligibility changed during processing; retry"
        )

    # -----------------------------------------------------
    # IMMUTABLE AUDIT
    # -----------------------------------------------------

    audit_row = await append_audit_log(
        db,
        actor_type=actor_type,
        actor_id=actor.id,
        actor_role=scope.role,
        actor_name=actor.full_name,
        actor_identifier=str(
            actor.id
        ),
        actor_email=actor.email,
        college=scope.college,
        department_id=scope.department_id,
        department_name=department_name,
        action="STUDENTS_ARCHIVED",
        description=(
            f"{actor.full_name} archived "
            f"{archived_count} graduated student(s) "
            f"from academic batch {batch.name} "
            f"after verified final export "
            f"#{export_job.id}."
        ),
        entity_type="academic_batch",
        entity_id=batch.id,
        source="website_portal",
        request=request,
        metadata={
            "batch_id": batch.id,
            "batch_name": batch.name,
            "scope_type": scope.scope_type.value,
            "department_id": scope.department_id,

            "transition": (
                "GRADUATED_TO_ARCHIVED"
            ),

            "verified_export_job_id": (
                export_job.id
            ),
            "verified_export_sha256": (
                export_job.sha256
            ),
            "verified_export_object_key": (
                export_job.object_key
            ),
            "verified_export_at": (
                export_job.verified_at.isoformat()
                if export_job.verified_at
                else None
            ),

            "selected_count": (
                preview.selected_count
            ),
            "eligible_count": (
                preview.eligible_count
            ),
            "archived_count": (
                archived_count
            ),
            "active_count": (
                preview.active_count
            ),
            "already_archived_count": (
                preview.archived_count
            ),
            "purged_count": (
                preview.purged_count
            ),
            "legacy_inactive_eligible_count": (
                preview.legacy_inactive_eligible_count
            ),
            "reason": reason,
        },
        commit=False,
    )

    return WebsiteBatchArchiveResponse(
        batch_id=batch.id,
        export_job_id=export_job.id,

        selected_count=preview.selected_count,
        eligible_count=preview.eligible_count,

        archived_count=archived_count,
        active_count=preview.active_count,
        already_archived_count=preview.archived_count,
        purged_count=preview.purged_count,

        legacy_inactive_eligible_count=(
            preview.legacy_inactive_eligible_count
        ),

        skipped_count=(
            preview.selected_count
            - archived_count
        ),

        audit_log_id=audit_row.id,
    )
