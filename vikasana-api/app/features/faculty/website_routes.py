from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    get_current_enabled_website_faculty_scope,
)
from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
)
from app.features.faculty.models import Faculty
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
    create_faculty,
)
from app.features.faculty.permission_service import (
    get_student_within_website_scope,
    list_students_within_website_scope,
)
from app.features.students.schemas.student import (
    StudentOut,
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
    College Coordinator, HOD, or Faculty Coordinator scope.

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

    HOD and Faculty Coordinator:
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
