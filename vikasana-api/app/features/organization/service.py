from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.college_access.models import CollegeAccessControl
from app.features.faculty.models import Faculty
from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_FACULTY,
    ROLE_HOD,
)
from app.features.students.models import Student

from app.features.organization.models import (
    AcademicBatch,
    College,
    CollegeAlias,
    CollegeOrganizationSetting,
    Department,
    FacultyAccessHistory,
    StudentAcademicHistory,
)
from app.features.organization.schemas import (
    AcademicBatchCreateRequest,
    AcademicBatchResponse,
    AcademicBatchUpdateRequest,
    CollegeCreateRequest,
    CollegeDetailsResponse,
    CollegeResponse,
    CollegeUpdateRequest,
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
)


# --------------------------------------------------
# SHARED HELPERS
# --------------------------------------------------


def normalize_college(college: str | None) -> str:
    normalized = (college or "").strip()

    if not normalized:
        raise HTTPException(
            status_code=400,
            detail="college is required",
        )

    return normalized


def _college_matches(column, college: str):
    return func.lower(func.trim(column)) == college.lower()


async def _commit_or_raise_conflict(
    db: AsyncSession,
    conflict_message: str,
) -> None:
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()

        raise HTTPException(
            status_code=409,
            detail=conflict_message,
        ) from error


# --------------------------------------------------
# COLLEGE MASTER
# --------------------------------------------------


async def _find_college(
    db: AsyncSession,
    college_id: int,
) -> College | None:
    result = await db.execute(
        select(College).where(
            College.id == college_id
        )
    )

    return result.scalar_one_or_none()


async def list_colleges(
    db: AsyncSession,
    include_inactive: bool = False,
) -> list[CollegeResponse]:
    statement = select(College)

    if not include_inactive:
        statement = statement.where(
            College.is_active.is_(True)
        )

    statement = statement.order_by(
        College.name.asc(),
        College.id.asc(),
    )

    result = await db.execute(statement)
    colleges = result.scalars().all()

    return [
        CollegeResponse.model_validate(college)
        for college in colleges
    ]


async def get_college(
    db: AsyncSession,
    college_id: int,
) -> CollegeResponse:
    college = await _find_college(
        db,
        college_id,
    )

    if college is None:
        raise HTTPException(
            status_code=404,
            detail="College not found",
        )

    return CollegeResponse.model_validate(college)


async def _college_scope_names(
    db: AsyncSession,
    college: College,
) -> list[str]:
    """
    Return the canonical college name plus active legacy aliases.

    This is read-only and allows historical records containing an
    old college spelling to remain untouched while still belonging
    to the canonical college in management views.
    """
    result = await db.execute(
        select(CollegeAlias.alias).where(
            CollegeAlias.college_id == college.id,
            CollegeAlias.is_active.is_(True),
        )
    )

    aliases = result.scalars().all()

    names = [
        college.name,
        *aliases,
    ]

    normalized_names: list[str] = []
    seen: set[str] = set()

    for name in names:
        normalized = (name or "").strip().lower()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        normalized_names.append(normalized)

    return normalized_names


async def get_college_details(
    db: AsyncSession,
    college_id: int,
) -> CollegeDetailsResponse:
    college = await _find_college(
        db,
        college_id,
    )

    if college is None:
        raise HTTPException(
            status_code=404,
            detail="College not found",
        )

    college_names = await _college_scope_names(
        db,
        college,
    )

    student_count = await db.scalar(
        select(
            func.count(Student.id)
        ).where(
            func.lower(
                func.trim(Student.college)
            ).in_(college_names)
        )
    )

    # Current organization hierarchy counts include active
    # accounts and pending-activation accounts.
    #
    # Explicitly removed/deactivated historical accounts remain
    # preserved in the database but are excluded from these counts.
    college_coordinator_count = await db.scalar(
        select(
            func.count(Faculty.id)
        ).where(
            func.lower(
                func.trim(Faculty.college)
            ).in_(college_names),
            func.lower(
                func.trim(Faculty.role)
            ) == ROLE_COLLEGE_COORDINATOR,
            or_(
                Faculty.is_active.is_(True),
                Faculty.activation_token_hash.is_not(None),
            ),
        )
    )

    hod_count = await db.scalar(
        select(
            func.count(Faculty.id)
        ).where(
            func.lower(
                func.trim(Faculty.college)
            ).in_(college_names),
            func.lower(
                func.trim(Faculty.role)
            ) == ROLE_HOD,
            or_(
                Faculty.is_active.is_(True),
                Faculty.activation_token_hash.is_not(None),
            ),
        )
    )

    faculty_count = await db.scalar(
        select(
            func.count(Faculty.id)
        ).where(
            func.lower(
                func.trim(Faculty.college)
            ).in_(college_names),
            func.lower(
                func.trim(Faculty.role)
            ) == ROLE_FACULTY,
            or_(
                Faculty.is_active.is_(True),
                Faculty.activation_token_hash.is_not(None),
            ),
        )
    )

    base = CollegeResponse.model_validate(
        college
    ).model_dump()

    return CollegeDetailsResponse(
        **base,
        student_count=int(student_count or 0),
        college_coordinator_count=int(
            college_coordinator_count or 0
        ),
        hod_count=int(hod_count or 0),
        faculty_count=int(faculty_count or 0),
    )



async def create_college(
    db: AsyncSession,
    payload: CollegeCreateRequest,
    *,
    created_by_admin_id: int | None = None,
) -> CollegeResponse:
    normalized_name = normalize_college(payload.name)

    college = College(
        name=normalized_name,
        code=payload.code,
        is_active=payload.is_active,
        created_by_admin_id=created_by_admin_id,
    )

    db.add(college)

    await _commit_or_raise_conflict(
        db,
        "A college with the same name or code already exists",
    )

    await db.refresh(college)

    return CollegeResponse.model_validate(college)


async def update_college(
    db: AsyncSession,
    college_id: int,
    payload: CollegeUpdateRequest,
) -> CollegeResponse:
    college = await _find_college(
        db,
        college_id,
    )

    if college is None:
        raise HTTPException(
            status_code=404,
            detail="College not found",
        )

    updates = payload.model_dump(
        exclude_unset=True,
    )

    if "name" in updates:
        updates["name"] = normalize_college(
            updates["name"]
        )

    for field_name, value in updates.items():
        setattr(
            college,
            field_name,
            value,
        )

    await _commit_or_raise_conflict(
        db,
        "A college with the same name or code already exists",
    )

    await db.refresh(college)

    return CollegeResponse.model_validate(college)



async def delete_college_master(
    db: AsyncSession,
    college_id: int,
    *,
    deleted_by_admin_id: int,
) -> CollegeResponse:
    """
    Permanently remove only the College master record.

    Historical hierarchy data is intentionally preserved:
    - Faculty / HOD / College Coordinator
    - Students
    - Departments
    - Academic batches
    - hierarchy / access history

    College-specific current configuration and College aliases are
    removed together with the College master record.

    Before removing the master, access is disabled for the canonical
    college name, active aliases, and existing Faculty/Student string
    variants so legacy accounts cannot continue using the platform.

    CollegeAlias rows are removed because they directly reference
    colleges.id with ON DELETE RESTRICT.
    """
    college = await _find_college(
        db,
        college_id,
    )

    if college is None:
        raise HTTPException(
            status_code=404,
            detail="College not found",
        )

    response = CollegeResponse.model_validate(
        college
    )

    alias_result = await db.execute(
        select(CollegeAlias.alias).where(
            CollegeAlias.college_id == college.id
        )
    )

    aliases = alias_result.scalars().all()

    canonical_scope_names = [
        college.name,
        *aliases,
    ]

    normalized_scope_names = [
        (name or "").strip().lower()
        for name in canonical_scope_names
        if (name or "").strip()
    ]

    access_names: list[str] = [
        (name or "").strip()
        for name in canonical_scope_names
        if (name or "").strip()
    ]

    if normalized_scope_names:
        faculty_variants_result = await db.execute(
            select(Faculty.college)
            .distinct()
            .where(
                func.lower(
                    func.trim(Faculty.college)
                ).in_(normalized_scope_names)
            )
        )

        student_variants_result = await db.execute(
            select(Student.college)
            .distinct()
            .where(
                func.lower(
                    func.trim(Student.college)
                ).in_(normalized_scope_names)
            )
        )

        access_names.extend(
            [
                (name or "").strip()
                for name in faculty_variants_result.scalars().all()
                if (name or "").strip()
            ]
        )

        access_names.extend(
            [
                (name or "").strip()
                for name in student_variants_result.scalars().all()
                if (name or "").strip()
            ]
        )

    deduplicated_access_names: list[str] = []
    seen_names: set[str] = set()

    for name in access_names:
        if name in seen_names:
            continue

        seen_names.add(name)
        deduplicated_access_names.append(name)

    now_utc = datetime.now(timezone.utc)

    reason = (
        f"College master deleted by Admin ID "
        f"{deleted_by_admin_id}"
    )

    # Block access for every known exact college-name variant.
    for access_name in deduplicated_access_names:
        result = await db.execute(
            select(CollegeAccessControl).where(
                CollegeAccessControl.college == access_name
            )
        )

        access_row = result.scalar_one_or_none()

        if access_row is None:
            access_row = CollegeAccessControl(
                college=access_name,
                is_active=False,
                reason=reason,
                deactivated_at=now_utc,
            )
            db.add(access_row)
        else:
            access_row.is_active = False
            access_row.reason = reason
            access_row.deactivated_at = now_utc
            access_row.updated_at = now_utc

    # Remove College-specific organization configuration.
    # This is current configuration, not historical hierarchy data.
    settings_result = await db.execute(
        select(CollegeOrganizationSetting).where(
            func.lower(
                func.trim(
                    CollegeOrganizationSetting.college
                )
            ).in_(normalized_scope_names)
        )
    )

    for settings_row in settings_result.scalars().all():
        await db.delete(settings_row)

    # Remove aliases belonging directly to this College master.
    alias_rows_result = await db.execute(
        select(CollegeAlias).where(
            CollegeAlias.college_id == college.id
        )
    )

    for alias_row in alias_rows_result.scalars().all():
        await db.delete(alias_row)

    # Remove only the College master row.
    # Faculty, Students, Departments, batches and history remain.
    await db.delete(college)

    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "College could not be deleted because "
                "a protected database relationship still exists"
            ),
        ) from error

    return response



# --------------------------------------------------
# ORGANISATION SETTINGS
# --------------------------------------------------


async def _find_organization_settings(
    db: AsyncSession,
    college: str,
) -> CollegeOrganizationSetting | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(CollegeOrganizationSetting).where(
            _college_matches(
                CollegeOrganizationSetting.college,
                normalized_college,
            )
        )
    )

    return result.scalar_one_or_none()


async def require_department_architecture_enabled(
    db: AsyncSession,
    college: str,
) -> CollegeOrganizationSetting:
    """
    Return the college organization settings when department mode
    is enabled.

    This helper performs only a read query. It does not create,
    update, commit, or refresh any database record.
    """
    from fastapi import HTTPException, status

    normalized_college = normalize_college(college)

    settings = await _find_organization_settings(
        db,
        normalized_college,
    )

    if (
        settings is None
        or not settings.department_architecture_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Department architecture is not enabled "
                "for this college"
            ),
        )

    return settings


async def get_organization_settings(
    db: AsyncSession,
    college: str,
) -> OrganizationSettingsResponse:
    normalized_college = normalize_college(college)

    settings = await _find_organization_settings(
        db,
        normalized_college,
    )

    if settings is None:
        return OrganizationSettingsResponse(
            id=None,
            college=normalized_college,
            department_architecture_enabled=False,
            academic_year_start_month=7,
            created_at=None,
            updated_at=None,
        )

    return OrganizationSettingsResponse.model_validate(settings)


async def update_organization_settings(
    db: AsyncSession,
    payload: OrganizationSettingsUpdateRequest,
) -> OrganizationSettingsResponse:
    normalized_college = normalize_college(payload.college)

    settings = await _find_organization_settings(
        db,
        normalized_college,
    )

    if settings is None:
        settings = CollegeOrganizationSetting(
            college=normalized_college,
            department_architecture_enabled=(
                payload.department_architecture_enabled
            ),
            academic_year_start_month=(
                payload.academic_year_start_month
            ),
        )

        db.add(settings)
    else:
        settings.college = normalized_college
        settings.department_architecture_enabled = (
            payload.department_architecture_enabled
        )
        settings.academic_year_start_month = (
            payload.academic_year_start_month
        )

    await _commit_or_raise_conflict(
        db,
        "Organization settings already exist for this college",
    )

    await db.refresh(settings)

    return OrganizationSettingsResponse.model_validate(settings)


# --------------------------------------------------
# DEPARTMENT HELPERS
# --------------------------------------------------


async def _find_department(
    db: AsyncSession,
    department_id: int,
    college: str,
) -> Department | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            _college_matches(
                Department.college,
                normalized_college,
            ),
        )
    )

    return result.scalar_one_or_none()


# --------------------------------------------------
# DEPARTMENT READ OPERATIONS
# --------------------------------------------------


async def list_departments(
    db: AsyncSession,
    college: str,
    include_inactive: bool = False,
) -> list[DepartmentResponse]:
    normalized_college = normalize_college(college)

    statement = select(Department).where(
        _college_matches(
            Department.college,
            normalized_college,
        )
    )

    if not include_inactive:
        statement = statement.where(
            Department.is_active.is_(True)
        )

    statement = statement.order_by(
        Department.name.asc(),
        Department.id.asc(),
    )

    result = await db.execute(statement)
    departments = result.scalars().all()

    return [
        DepartmentResponse.model_validate(department)
        for department in departments
    ]


async def get_department(
    db: AsyncSession,
    department_id: int,
    college: str,
) -> DepartmentResponse:
    department = await _find_department(
        db,
        department_id,
        college,
    )

    if department is None:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    return DepartmentResponse.model_validate(department)


# --------------------------------------------------
# DEPARTMENT WRITE OPERATIONS
# --------------------------------------------------


async def create_department(
    db: AsyncSession,
    payload: DepartmentCreateRequest,
    *,
    created_by_admin_id: int | None = None,
    created_by_faculty_id: int | None = None,
) -> DepartmentResponse:
    normalized_college = normalize_college(payload.college)

    department = Department(
        college=normalized_college,
        name=payload.name,
        code=payload.code,
        is_active=payload.is_active,
        created_by_admin_id=created_by_admin_id,
        created_by_faculty_id=created_by_faculty_id,
    )

    db.add(department)

    await _commit_or_raise_conflict(
        db,
        (
            "A department with the same name or code "
            "already exists for this college"
        ),
    )

    await db.refresh(department)

    return DepartmentResponse.model_validate(department)


async def update_department(
    db: AsyncSession,
    department_id: int,
    college: str,
    payload: DepartmentUpdateRequest,
) -> DepartmentResponse:
    department = await _find_department(
        db,
        department_id,
        college,
    )

    if department is None:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    updates = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    for field_name, value in updates.items():
        setattr(department, field_name, value)

    await _commit_or_raise_conflict(
        db,
        (
            "A department with the same name or code "
            "already exists for this college"
        ),
    )

    await db.refresh(department)

    return DepartmentResponse.model_validate(department)


async def delete_department(
    db: AsyncSession,
    department_id: int,
    college: str,
) -> DepartmentResponse:
    """
    Permanently delete an unused Department master.

    A Department that has ever been referenced by Faculty,
    Students, Faculty access history, or Student academic
    history cannot be deleted because doing so would weaken
    the hierarchy/audit trail. Used departments must instead
    be deactivated.
    """
    department = await _find_department(
        db,
        department_id,
        college,
    )

    if department is None:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    faculty_count_result = await db.execute(
        select(func.count(Faculty.id)).where(
            Faculty.department_id == department.id
        )
    )

    faculty_count = int(
        faculty_count_result.scalar() or 0
    )

    student_count_result = await db.execute(
        select(func.count(Student.id)).where(
            Student.department_id == department.id
        )
    )

    student_count = int(
        student_count_result.scalar() or 0
    )

    faculty_history_result = await db.execute(
        select(
            func.count(
                FacultyAccessHistory.id
            )
        ).where(
            FacultyAccessHistory.department_id
            == department.id
        )
    )

    faculty_history_count = int(
        faculty_history_result.scalar() or 0
    )

    student_history_result = await db.execute(
        select(
            func.count(
                StudentAcademicHistory.id
            )
        ).where(
            (
                StudentAcademicHistory.from_department_id
                == department.id
            )
            |
            (
                StudentAcademicHistory.to_department_id
                == department.id
            )
        )
    )

    student_history_count = int(
        student_history_result.scalar() or 0
    )

    total_references = (
        faculty_count
        + student_count
        + faculty_history_count
        + student_history_count
    )

    if total_references > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Department cannot be deleted because it is in use. "
                f"Faculty: {faculty_count}, "
                f"Students: {student_count}, "
                f"Faculty history: {faculty_history_count}, "
                f"Student history: {student_history_count}. "
                "Deactivate the department instead."
            ),
        )

    response = DepartmentResponse.model_validate(
        department
    )

    await db.delete(department)

    await _commit_or_raise_conflict(
        db,
        (
            "Department could not be deleted because "
            "a protected relationship still exists"
        ),
    )

    return response


# --------------------------------------------------
# ACADEMIC BATCH HELPERS
# --------------------------------------------------


async def _find_academic_batch(
    db: AsyncSession,
    batch_id: int,
    college: str,
) -> AcademicBatch | None:
    normalized_college = normalize_college(college)

    result = await db.execute(
        select(AcademicBatch).where(
            AcademicBatch.id == batch_id,
            _college_matches(
                AcademicBatch.college,
                normalized_college,
            ),
        )
    )

    return result.scalar_one_or_none()


def _validate_batch_year_order(
    admitted_year: int,
    passout_year: int,
) -> None:
    if passout_year <= admitted_year:
        raise HTTPException(
            status_code=400,
            detail="passout_year must be greater than admitted_year",
        )


# --------------------------------------------------
# ACADEMIC BATCH READ OPERATIONS
# --------------------------------------------------


async def list_academic_batches(
    db: AsyncSession,
    college: str,
    include_inactive: bool = False,
) -> list[AcademicBatchResponse]:
    normalized_college = normalize_college(college)

    statement = select(AcademicBatch).where(
        _college_matches(
            AcademicBatch.college,
            normalized_college,
        )
    )

    if not include_inactive:
        statement = statement.where(
            AcademicBatch.is_active.is_(True)
        )

    statement = statement.order_by(
        AcademicBatch.admitted_year.desc(),
        AcademicBatch.passout_year.desc(),
        AcademicBatch.id.desc(),
    )

    result = await db.execute(statement)
    batches = result.scalars().all()

    return [
        AcademicBatchResponse.model_validate(batch)
        for batch in batches
    ]


async def get_academic_batch(
    db: AsyncSession,
    batch_id: int,
    college: str,
) -> AcademicBatchResponse:
    batch = await _find_academic_batch(
        db,
        batch_id,
        college,
    )

    if batch is None:
        raise HTTPException(
            status_code=404,
            detail="Academic batch not found",
        )

    return AcademicBatchResponse.model_validate(batch)


# --------------------------------------------------
# ACADEMIC BATCH WRITE OPERATIONS
# --------------------------------------------------


async def create_academic_batch(
    db: AsyncSession,
    payload: AcademicBatchCreateRequest,
    *,
    created_by_admin_id: int | None = None,
    created_by_faculty_id: int | None = None,
) -> AcademicBatchResponse:
    normalized_college = normalize_college(payload.college)

    _validate_batch_year_order(
        payload.admitted_year,
        payload.passout_year,
    )

    batch = AcademicBatch(
        college=normalized_college,
        name=payload.name,
        admitted_year=payload.admitted_year,
        passout_year=payload.passout_year,
        course_duration_years=payload.course_duration_years,
        is_active=payload.is_active,
        created_by_admin_id=created_by_admin_id,
        created_by_faculty_id=created_by_faculty_id,
    )

    db.add(batch)

    await _commit_or_raise_conflict(
        db,
        (
            "An academic batch with the same years and course "
            "duration already exists for this college"
        ),
    )

    await db.refresh(batch)

    return AcademicBatchResponse.model_validate(batch)


async def update_academic_batch(
    db: AsyncSession,
    batch_id: int,
    college: str,
    payload: AcademicBatchUpdateRequest,
) -> AcademicBatchResponse:
    batch = await _find_academic_batch(
        db,
        batch_id,
        college,
    )

    if batch is None:
        raise HTTPException(
            status_code=404,
            detail="Academic batch not found",
        )

    updates = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    effective_admitted_year = updates.get(
        "admitted_year",
        batch.admitted_year,
    )

    effective_passout_year = updates.get(
        "passout_year",
        batch.passout_year,
    )

    _validate_batch_year_order(
        effective_admitted_year,
        effective_passout_year,
    )

    for field_name, value in updates.items():
        setattr(batch, field_name, value)

    await _commit_or_raise_conflict(
        db,
        (
            "An academic batch with the same years and course "
            "duration already exists for this college"
        ),
    )

    await db.refresh(batch)

    return AcademicBatchResponse.model_validate(batch)