from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    get_current_enabled_website_faculty_scope,
)
from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
)
from app.features.faculty.permission_service import (
    get_student_within_website_scope,
    list_students_within_website_scope,
)
from app.features.students.schemas.student import (
    StudentOut,
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
