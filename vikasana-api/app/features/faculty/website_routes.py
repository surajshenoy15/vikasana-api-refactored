from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import and_, delete, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.dependencies import (
    get_current_enabled_website_faculty_scope,
)
from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.faculty.models import (
    Faculty,
    FacultyActivationSession,
)
from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_FACULTY,
    ROLE_HOD,
)
from app.features.faculty.schemas.faculty import (
    FacultyCreateRequest,
    FacultyCreateResponse,
    FacultyResponse,
)
from app.features.faculty.schemas.website_hierarchy import (
    WebsiteDepartmentCreateRequest,
    WebsiteFacultyMentorCreateRequest,
    WebsiteHODCreateRequest,
)
from app.features.faculty.service import (
    add_faculty_access_history,
    create_faculty,
)
from app.features.faculty.permission_service import (
    get_student_within_website_scope,
    list_students_within_website_scope,
)
from app.features.students.schemas.student import (
    StudentOut,
)
from app.features.students.models import (
    Student,
    StudentType,
)
from app.features.certificates.models import Certificate
from app.features.events.models import (
    EventParticipant,
    ExternalParticipantLinkHistory,
)
from app.features.organization.models import (
    Department,
    FacultyAccessHistory,
)
from app.features.organization.schemas import (
    DepartmentCreateRequest,
    DepartmentResponse,
)
from app.features.organization.service import (
    create_department,
    list_departments,
)

from app.features.faculty.dashboard_service import (
    get_website_faculty_dashboard_stats as get_scoped_dashboard_stats,
)

from app.features.faculty.schemas.website_dashboard import (
    WebsiteFacultyDashboardStatsOut,
)


router = APIRouter(
    prefix="/website/faculty",
    tags=["Website - Faculty"],
)



def _not_explicitly_removed_faculty():
    """
    Keep active and pending accounts visible while hiding accounts
    explicitly removed through an immutable deactivation history row.
    """

    has_deactivation_history = (
        select(
            FacultyAccessHistory.id
        )
        .where(
            FacultyAccessHistory.faculty_id
            == Faculty.id,
            FacultyAccessHistory.action
            == "deactivated",
        )
        .exists()
    )

    return not_(
        and_(
            Faculty.is_active.is_(False),
            Faculty.activation_token_hash.is_(None),
            has_deactivation_history,
        )
    )


class WebsiteMentorStudentUpdateRequest(BaseModel):
    name: str | None = None
    usn: str | None = None
    email: EmailStr | None = None
    branch: str | None = None
    student_type: str | None = None

    current_year: int | None = None
    admitted_year: int | None = None
    passout_year: int | None = None


@router.get(
    "/departments",
    response_model=list[DepartmentResponse],
    summary="List departments for College Coordinator",
)
async def list_website_departments(
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[DepartmentResponse]:
    """
    List active departments belonging to the authenticated
    College Coordinator's college.
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a College Coordinator can manage "
                "college departments"
            ),
        )

    return await list_departments(
        db,
        scope.college,
        include_inactive=False,
    )


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create department under College Coordinator",
)
async def create_website_department(
    payload: WebsiteDepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> DepartmentResponse:
    """
    Create a department inside the authenticated College
    Coordinator's college.

    College and creator provenance are derived from the
    authenticated scope and cannot be supplied by the client.
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a College Coordinator can create "
                "a department"
            ),
        )

    create_payload = DepartmentCreateRequest(
        college=scope.college,
        name=payload.name,
        code=payload.code,
        is_active=True,
    )

    return await create_department(
        db,
        create_payload,
        created_by_faculty_id=scope.faculty_id,
    )



# ------------------------------------------------------------
# COLLEGE COORDINATOR -> DEPARTMENT DRILL-DOWN
# ------------------------------------------------------------


async def _get_coordinator_department(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    department_id: int,
) -> Department:
    """
    Resolve one active department only when it belongs to the
    authenticated College Coordinator's college.

    This helper is read-only.
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a College Coordinator can access "
                "department drill-down details"
            ),
        )

    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            func.lower(
                func.trim(Department.college)
            )
            == scope.college.strip().casefold(),
            Department.is_active.is_(True),
        )
    )

    department = result.scalar_one_or_none()

    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found",
        )

    return department


@router.get(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
    summary="Get department details for College Coordinator",
)
async def get_website_department_details(
    department_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> DepartmentResponse:
    """
    Return one active department within the authenticated
    College Coordinator's college.
    """

    department = await _get_coordinator_department(
        db=db,
        scope=scope,
        department_id=department_id,
    )

    return DepartmentResponse.model_validate(
        department
    )


@router.get(
    "/departments/{department_id}/hods",
    response_model=list[FacultyResponse],
    summary="List department HODs for College Coordinator",
)
async def list_website_department_hods(
    department_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[FacultyResponse]:
    """
    Read-only department HOD listing.

    HOD hierarchy remains scoped to the authenticated
    College Coordinator through parent_faculty_id.
    """

    department = await _get_coordinator_department(
        db=db,
        scope=scope,
        department_id=department_id,
    )

    result = await db.execute(
        select(Faculty)
        .where(
            Faculty.college == scope.college,
            Faculty.department_id == department.id,
            Faculty.role == ROLE_HOD,
            Faculty.parent_faculty_id
            == scope.faculty_id,
            _not_explicitly_removed_faculty(),
        )
        .order_by(
            Faculty.created_at.desc()
        )
    )

    return [
        FacultyResponse.model_validate(item)
        for item in result.scalars().all()
    ]


@router.get(
    "/departments/{department_id}/mentors",
    response_model=list[FacultyResponse],
    summary=(
        "List department Faculty/Mentors "
        "for College Coordinator"
    ),
)
async def list_website_department_mentors(
    department_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[FacultyResponse]:
    """
    Read-only Faculty/Mentor listing for a selected department.

    College Coordinator receives no Faculty/Mentor write capability
    through this endpoint.
    """

    department = await _get_coordinator_department(
        db=db,
        scope=scope,
        department_id=department_id,
    )

    result = await db.execute(
        select(Faculty)
        .where(
            Faculty.college == scope.college,
            Faculty.department_id == department.id,
            Faculty.role == ROLE_FACULTY,
        )
        .order_by(
            Faculty.full_name.asc(),
            Faculty.id.asc(),
        )
    )

    return [
        FacultyResponse.model_validate(item)
        for item in result.scalars().all()
    ]


@router.get(
    "/departments/{department_id}/students",
    response_model=list[StudentOut],
    summary=(
        "List department students "
        "for College Coordinator"
    ),
)
async def list_website_department_students(
    department_id: int = Path(
        ...,
        ge=1,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[StudentOut]:
    """
    Read-only students for one selected department.

    Filtering happens server-side so large colleges are not loaded
    into the browser before department filtering.
    """

    department = await _get_coordinator_department(
        db=db,
        scope=scope,
        department_id=department_id,
    )

    result = await db.execute(
        select(Student)
        .where(
            func.lower(
                func.trim(Student.college)
            )
            == scope.college.strip().casefold(),
            Student.department_id
            == department.id,
        )
        .order_by(
            Student.id.asc()
        )
        .offset(offset)
        .limit(limit)
    )

    return list(
        result.scalars().all()
    )


@router.get(
    "/hods",
    response_model=list[FacultyResponse],
    summary="List HODs under College Coordinator",
)
async def list_website_hods(
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[FacultyResponse]:
    """
    List HOD accounts directly under the authenticated
    College Coordinator.

    The hierarchy is enforced using parent_faculty_id.
    This endpoint is read-only.
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a College Coordinator can list "
                "college HOD accounts"
            ),
        )

    result = await db.execute(
        select(Faculty)
        .where(
            Faculty.college == scope.college,
            Faculty.role == ROLE_HOD,
            Faculty.parent_faculty_id == scope.faculty_id,
            _not_explicitly_removed_faculty(),
        )
        .order_by(Faculty.created_at.desc())
    )

    items = result.scalars().all()

    return [
        FacultyResponse.model_validate(item)
        for item in items
    ]


@router.post(
    "/hods",
    response_model=FacultyCreateResponse,
    summary="Create HOD under College Coordinator",
)
async def create_website_hod(
    payload: WebsiteHODCreateRequest,
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> FacultyCreateResponse:
    """
    Create an HOD under the authenticated College Coordinator.

    The client supplies only:
        full_name
        email
        department_id

    The backend derives and locks:
        college
        role
        parent_faculty_id
        created_by_faculty_id
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only a College Coordinator can create "
                "an HOD"
            ),
        )

    create_payload = FacultyCreateRequest(
        full_name=payload.full_name,
        college=scope.college,
        email=payload.email,
        role=ROLE_HOD,
        department_id=payload.department_id,
    )

    faculty, email_sent = await create_faculty(
        payload=create_payload,
        db=db,
        parent_faculty_id=scope.faculty_id,
        created_by_faculty_id=scope.faculty_id,
    )

    message = (
        "HOD created and activation email sent."
        if email_sent
        else (
            "HOD created, but activation email "
            "could not be sent."
        )
    )

    return FacultyCreateResponse(
        faculty=FacultyResponse.model_validate(
            faculty
        ),
        activation_email_sent=email_sent,
        message=message,
    )



@router.delete(
    "/hods/{hod_id}",
    summary="Remove HOD under College Coordinator",
)
async def remove_website_hod(
    hod_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Non-destructively remove an HOD directly owned by the
    authenticated College Coordinator.

    Historical Faculty, hierarchy and provenance rows are preserved.
    """

    if scope.role != ROLE_COLLEGE_COORDINATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a College Coordinator can remove "
                "an HOD"
            ),
        )

    result = await db.execute(
        select(Faculty).where(
            Faculty.id == hod_id,
            Faculty.college == scope.college,
            Faculty.role == ROLE_HOD,
            Faculty.parent_faculty_id
            == scope.faculty_id,
        )
    )

    hod = result.scalar_one_or_none()

    if hod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="HOD not found",
        )

    previous_deactivation = await db.scalar(
        select(
            FacultyAccessHistory.id
        )
        .where(
            FacultyAccessHistory.faculty_id
            == hod.id,
            FacultyAccessHistory.action
            == "deactivated",
        )
        .order_by(
            FacultyAccessHistory.created_at.desc(),
            FacultyAccessHistory.id.desc(),
        )
        .limit(1)
    )

    already_removed = (
        hod.is_active is False
        and hod.activation_token_hash is None
        and previous_deactivation is not None
    )

    if already_removed:
        return {
            "success": True,
            "message": "HOD is already removed",
            "hod_id": hod.id,
            "is_active": False,
            "activation_revoked": True,
        }

    hod.is_active = False
    hod.activation_token_hash = None
    hod.activation_expires_at = None

    await db.execute(
        delete(
            FacultyActivationSession
        ).where(
            FacultyActivationSession.faculty_id
            == hod.id
        )
    )

    await add_faculty_access_history(
        db=db,
        faculty_id=hod.id,
        action="deactivated",
        college=hod.college,
        previous_role=hod.role,
        new_role=hod.role,
        previous_parent_faculty_id=(
            hod.parent_faculty_id
        ),
        new_parent_faculty_id=(
            hod.parent_faculty_id
        ),
        department_id=hod.department_id,
        changed_by_faculty_id=(
            scope.faculty_id
        ),
        note=(
            "HOD access removed by College Coordinator; "
            "historical hierarchy and provenance preserved"
        ),
    )

    try:
        await db.commit()
        await db.refresh(hod)
    except Exception:
        await db.rollback()
        raise

    return {
        "success": True,
        "message": "HOD removed successfully",
        "hod_id": hod.id,
        "is_active": hod.is_active,
        "activation_revoked": True,
    }


@router.get(
    "/mentors",
    response_model=list[FacultyResponse],
    summary="List Faculty/Mentors under HOD",
)
async def list_website_faculty_mentors(
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[FacultyResponse]:
    """
    List Faculty/Mentor accounts directly under the
    authenticated HOD.

    College, department and parent hierarchy are enforced
    from the authenticated scope. This endpoint is read-only.
    """

    if scope.role != ROLE_HOD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only an HOD can list "
                "Faculty/Mentor accounts"
            ),
        )

    if scope.department_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HOD department is not assigned",
        )

    result = await db.execute(
        select(Faculty)
        .where(
            Faculty.college == scope.college,
            Faculty.department_id == scope.department_id,
            Faculty.role == ROLE_FACULTY,
            Faculty.parent_faculty_id == scope.faculty_id,
        )
        .order_by(Faculty.created_at.desc())
    )

    items = result.scalars().all()

    return [
        FacultyResponse.model_validate(item)
        for item in items
    ]


@router.post(
    "/mentors",
    response_model=FacultyCreateResponse,
    summary="Create Faculty/Mentor under HOD",
)
async def create_website_faculty_mentor(
    payload: WebsiteFacultyMentorCreateRequest,
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> FacultyCreateResponse:
    """
    Create a Faculty/Mentor account under the
    authenticated HOD.

    College, department, role, parent and creator
    provenance are derived entirely from the HOD scope.
    """

    if scope.role != ROLE_HOD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only an HOD can create "
                "Faculty/Mentor accounts"
            ),
        )

    if scope.department_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HOD department is not assigned",
        )

    create_payload = FacultyCreateRequest(
        full_name=payload.full_name,
        college=scope.college,
        email=payload.email,
        role=ROLE_FACULTY,
        department_id=scope.department_id,
    )

    faculty, email_sent = await create_faculty(
        payload=create_payload,
        db=db,
        parent_faculty_id=scope.faculty_id,
        created_by_faculty_id=scope.faculty_id,
    )

    message = (
        "Faculty/Mentor created and activation email sent."
        if email_sent
        else (
            "Faculty/Mentor created, but activation "
            "email could not be sent."
        )
    )

    return FacultyCreateResponse(
        faculty=FacultyResponse.model_validate(
            faculty
        ),
        activation_email_sent=email_sent,
        message=message,
    )



# ------------------------------------------------------------
# HOD -> FACULTY/MENTOR -> STUDENTS
# ------------------------------------------------------------


async def _get_hod_owned_mentor(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    mentor_id: int,
) -> Faculty:
    """
    Resolve a Faculty/Mentor only when it belongs directly to the
    authenticated HOD in the same college and department.
    """

    if scope.role != ROLE_HOD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only an HOD can manage "
                "Faculty/Mentor students"
            ),
        )

    if scope.department_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HOD department is not assigned",
        )

    result = await db.execute(
        select(Faculty).where(
            Faculty.id == mentor_id,
            Faculty.college == scope.college,
            Faculty.department_id == scope.department_id,
            Faculty.role == ROLE_FACULTY,
            Faculty.parent_faculty_id == scope.faculty_id,
        )
    )

    mentor = result.scalar_one_or_none()

    if mentor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Faculty/Mentor not found",
        )

    return mentor


@router.get(
    "/mentors/{mentor_id}/students",
    response_model=list[StudentOut],
    summary="List students assigned to Faculty/Mentor",
)
async def list_website_mentor_students(
    mentor_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[StudentOut]:
    """
    List students assigned to one Faculty/Mentor directly under
    the authenticated HOD.
    """

    await _get_hod_owned_mentor(
        db=db,
        scope=scope,
        mentor_id=mentor_id,
    )

    statement = select(Student).where(
        Student.assigned_faculty_id == mentor_id
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    statement = statement.order_by(
        Student.name.asc(),
        Student.id.asc(),
    )

    result = await db.execute(statement)

    return list(result.scalars().all())


@router.patch(
    "/mentors/{mentor_id}/students/{student_id}",
    response_model=StudentOut,
    summary="Update student under Faculty/Mentor",
)
async def update_website_mentor_student(
    mentor_id: int = Path(
        ...,
        ge=1,
    ),
    student_id: int = Path(
        ...,
        ge=1,
    ),
    payload: WebsiteMentorStudentUpdateRequest = ...,
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> StudentOut:
    """
    Allow an authenticated HOD to edit student information only
    when the Faculty/Mentor belongs directly to that HOD and the
    student is assigned to that Faculty/Mentor.

    College, department, batch, and Faculty/Mentor assignment
    cannot be changed through this endpoint.
    """

    await _get_hod_owned_mentor(
        db=db,
        scope=scope,
        mentor_id=mentor_id,
    )

    student = await get_student_within_website_scope(
        db=db,
        scope=scope,
        student_id=student_id,
    )

    if student.assigned_faculty_id != mentor_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Student is not assigned to this "
                "Faculty/Mentor"
            ),
        )

    data = payload.model_dump(
        exclude_unset=True,
    )

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No student fields provided for update",
        )

    if "name" in data:
        name = str(
            data["name"] or ""
        ).strip()

        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Student name is required",
            )

        data["name"] = name

    if "usn" in data:
        usn = str(
            data["usn"] or ""
        ).strip().upper()

        if not usn:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="USN is required",
            )

        duplicate = await db.execute(
            select(Student.id).where(
                Student.college == student.college,
                Student.usn == usn,
                Student.id != student.id,
            )
        )

        if duplicate.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="USN already exists",
            )

        data["usn"] = usn

    if "email" in data and data["email"] is not None:
        email = str(
            data["email"]
        ).strip().lower()

        duplicate = await db.execute(
            select(Student.id).where(
                Student.college == student.college,
                Student.email == email,
                Student.id != student.id,
            )
        )

        if duplicate.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already exists",
            )

        data["email"] = email

    if "branch" in data:
        branch = str(
            data["branch"] or ""
        ).strip()

        if not branch:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Branch is required",
            )

        data["branch"] = branch

    if "student_type" in data:
        student_type = str(
            data["student_type"] or ""
        ).strip().upper()

        if student_type not in (
            StudentType.REGULAR.value,
            StudentType.DIPLOMA.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "student_type must be "
                    "REGULAR or DIPLOMA"
                ),
            )

        data["student_type"] = student_type

    if (
        "current_year" in data
        and data["current_year"] is not None
        and not 1 <= data["current_year"] <= 8
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Current year must be between 1 and 8",
        )

    if (
        "admitted_year" in data
        and data["admitted_year"] is not None
        and not 1990 <= data["admitted_year"] <= 2100
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid admission year",
        )

    if (
        "passout_year" in data
        and data["passout_year"] is not None
        and not 1990 <= data["passout_year"] <= 2100
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid passout year",
        )

    final_admitted_year = data.get(
        "admitted_year",
        student.admitted_year,
    )

    final_passout_year = data.get(
        "passout_year",
        student.passout_year,
    )

    if (
        final_admitted_year is not None
        and final_passout_year is not None
        and final_passout_year
        < final_admitted_year
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Passout year cannot be before "
                "admission year"
            ),
        )

    for field_name, value in data.items():
        setattr(
            student,
            field_name,
            value,
        )

    await db.commit()
    await db.refresh(student)

    return student


@router.delete(
    "/mentors/{mentor_id}/students/{student_id}",
    summary="Permanently delete student under Faculty/Mentor",
)
async def delete_website_mentor_student(
    mentor_id: int = Path(
        ...,
        ge=1,
    ),
    student_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Permanently delete a student assigned to a Faculty/Mentor
    directly under the authenticated HOD.

    This is a hard delete, not an unassign operation.
    """

    await _get_hod_owned_mentor(
        db=db,
        scope=scope,
        mentor_id=mentor_id,
    )

    student = await get_student_within_website_scope(
        db=db,
        scope=scope,
        student_id=student_id,
    )

    if student.assigned_faculty_id != mentor_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Student is not assigned to this "
                "Faculty/Mentor"
            ),
        )

    # Historical external-event participation must never be destroyed.
    current_participant_result = await db.execute(
        select(EventParticipant.id)
        .where(
            EventParticipant.student_id == student_id
        )
        .limit(1)
    )

    historical_link_result = await db.execute(
        select(ExternalParticipantLinkHistory.id)
        .where(
            ExternalParticipantLinkHistory.student_id == student_id
        )
        .limit(1)
    )

    if (
        current_participant_result.scalar_one_or_none() is not None
        or historical_link_result.scalar_one_or_none() is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Student cannot be permanently deleted because "
                "historical external event participation is linked "
                "to this account. Deactivate the student instead."
            ),
        )

    try:
        # Certificates do not currently use ON DELETE CASCADE.
        await db.execute(
            delete(Certificate).where(
                Certificate.student_id == student_id
            )
        )

        # Other student-linked hierarchy/activity/face records
        # use database ON DELETE CASCADE.
        await db.execute(
            delete(Student).where(
                Student.id == student_id
            )
        )

        await db.commit()

        return {
            "success": True,
            "message": (
                "Student permanently deleted successfully"
            ),
            "student_id": student_id,
            "mentor_id": mentor_id,
        }

    except Exception as error:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete student permanently: "
                f"{str(error)}"
            ),
        )


@router.get(
    "/dashboard/stats",
    response_model=WebsiteFacultyDashboardStatsOut,
    summary="Website Faculty dashboard statistics",
)
async def get_website_faculty_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteFacultyDashboardStatsOut:
    """
    Return read-only dashboard statistics within the authenticated
    College Coordinator or HOD scope.

    Department architecture must be enabled for the Faculty college.
    """
    return await get_scoped_dashboard_stats(
        db=db,
        scope=scope,
    )


@router.get(
    "/students",
    response_model=list[StudentOut],
)
async def list_website_faculty_students(
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
) -> list[StudentOut]:
    """
    List students visible to the authenticated website Faculty role.

    College Coordinator:
        all students in the same college.

    HOD:
        all students in the same college and department.
    """
    return await list_students_within_website_scope(
        db=db,
        scope=scope,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/students/{student_id}",
    response_model=StudentOut,
)
async def get_website_faculty_student(
    student_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> StudentOut:
    """
    Return one student only when the student belongs to the
    authenticated website Faculty scope.

    Missing and outside-scope students both return the same 404.
    """
    return await get_student_within_website_scope(
        db=db,
        scope=scope,
        student_id=student_id,
    )
