from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import and_, delete, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.minio_client import get_presigned_url
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
    BatchExportJob,
    BatchPurgeJob,
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
from app.features.faculty.batch_export_service import (
    create_batch_export_job,
    get_batch_export_job_within_scope,
    get_latest_batch_export_job_within_scope,
)

from app.features.faculty.website_batch_purge_service import (
    create_batch_purge_job,
    get_batch_purge_job_within_scope,
    get_latest_batch_purge_job_within_scope,
    preview_website_batch_purge,
)

from app.features.faculty.website_batch_management_service import (
    archive_website_batch_graduated_students,
    graduate_website_batch_final_year,
    list_website_batch_students,
    list_website_batch_summaries,
    move_website_student_year,
    preview_website_batch_archive,
    preview_website_batch_graduation,
    preview_website_batch_year_promotion,
    promote_entire_website_batch_year,
    promote_selected_website_batch_students,
)

from app.features.faculty.schemas.website_dashboard import (
    WebsiteFacultyDashboardStatsOut,
)
from app.features.faculty.schemas.website_batch_management import (
    WebsiteBatchArchivePreview,
    WebsiteBatchPurgePreview,
    WebsiteBatchPurgeRequest,
    WebsiteBatchPurgeJobResponse,
    WebsiteBatchArchiveRequest,
    WebsiteBatchArchiveResponse,
    WebsiteBatchGraduateRequest,
    WebsiteBatchGraduationResponse,
    WebsiteBatchGraduationPreview,
    WebsiteBatchPromotionPreview,
    WebsiteBatchPromotionResponse,
    WebsiteBatchPromoteSelectedRequest,
    WebsiteBatchPromoteYearRequest,
    WebsiteBatchStudentPage,
    WebsiteBatchSummary,
    WebsiteStudentMoveYearRequest,
    WebsiteStudentMoveYearResponse,
    WebsiteBatchExportDownloadResponse,
    WebsiteBatchExportJobResponse,
)



def _website_batch_export_response(
    job: BatchExportJob,
) -> WebsiteBatchExportJobResponse:
    return WebsiteBatchExportJobResponse(
        id=int(job.id),
        batch_id=int(job.batch_id),
        scope_type=str(job.scope_type),
        department_id=job.department_id,
        status=str(job.status),

        students_rows=int(
            job.students_rows or 0
        ),
        academic_history_rows=int(
            job.academic_history_rows or 0
        ),
        activity_records_rows=int(
            job.activity_records_rows or 0
        ),
        activity_points_rows=int(
            job.activity_points_rows or 0
        ),
        event_participation_rows=int(
            job.event_participation_rows or 0
        ),
        certificates_rows=int(
            job.certificates_rows or 0
        ),

        file_size_bytes=job.file_size_bytes,
        sha256=job.sha256,

        started_at=job.started_at,
        completed_at=job.completed_at,
        verified_at=job.verified_at,

        failure_reason=job.failure_reason,
        created_at=job.created_at,
    )



def _website_batch_purge_response(
    job: BatchPurgeJob,
) -> WebsiteBatchPurgeJobResponse:
    return WebsiteBatchPurgeJobResponse(
        id=int(job.id),
        batch_id=int(job.batch_id),
        export_job_id=int(
            job.export_job_id
        ),

        scope_type=str(
            job.scope_type
        ),
        department_id=(
            int(job.department_id)
            if job.department_id is not None
            else None
        ),

        reason=str(
            job.reason
        ),
        status=str(
            job.status
        ),

        students_targeted=int(
            job.students_targeted or 0
        ),
        students_purged=int(
            job.students_purged or 0
        ),

        activity_sessions_deleted=int(
            job.activity_sessions_deleted or 0
        ),
        activity_photos_deleted=int(
            job.activity_photos_deleted or 0
        ),
        activity_face_checks_deleted=int(
            job.activity_face_checks_deleted or 0
        ),
        event_submission_photos_deleted=int(
            job.event_submission_photos_deleted or 0
        ),

        face_embeddings_deleted=int(
            job.face_embeddings_deleted or 0
        ),
        face_enrollment_images_deleted=int(
            job.face_enrollment_images_deleted or 0
        ),

        push_devices_deleted=int(
            job.push_devices_deleted or 0
        ),
        notification_deliveries_deleted=int(
            job.notification_deliveries_deleted or 0
        ),

        event_role_assignments_deleted=int(
            job.event_role_assignments_deleted or 0
        ),
        faculty_assignments_deleted=int(
            job.faculty_assignments_deleted or 0
        ),

        activity_progress_deleted=int(
            job.activity_progress_deleted or 0
        ),
        activity_stats_deleted=int(
            job.activity_stats_deleted or 0
        ),
        point_adjustments_deleted=int(
            job.point_adjustments_deleted or 0
        ),

        storage_objects_targeted=int(
            job.storage_objects_targeted or 0
        ),
        storage_objects_deleted=int(
            job.storage_objects_deleted or 0
        ),
        ignored_storage_metadata=int(
            job.ignored_storage_metadata or 0
        ),
        unsafe_storage_references=int(
            job.unsafe_storage_references or 0
        ),

        started_at=job.started_at,
        storage_completed_at=(
            job.storage_completed_at
        ),
        database_completed_at=(
            job.database_completed_at
        ),
        completed_at=job.completed_at,

        failure_reason=job.failure_reason,
        created_at=job.created_at,
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



# =========================================================
# BATCH MANAGEMENT - READ ONLY
# =========================================================


@router.get(
    "/batches",
    response_model=list[WebsiteBatchSummary],
    summary="List academic batches within Website Faculty scope",
)
async def list_website_faculty_batches(
    include_inactive: bool = Query(
        default=False,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> list[WebsiteBatchSummary]:
    """
    Return academic batch summaries inside the authenticated
    Website Faculty authorization scope.

    College Coordinator:
        counts Students across the authenticated college.

    HOD:
        counts only Students belonging to the authenticated
        HOD department.

    AcademicBatch remains college-wide.

    This endpoint is read-only and performs no database mutation.
    """
    return await list_website_batch_summaries(
        db=db,
        scope=scope,
        include_inactive=include_inactive,
    )


@router.get(
    "/batches/{batch_id}/students",
    response_model=WebsiteBatchStudentPage,
    summary="List students within an academic batch and Website Faculty scope",
)
async def list_website_faculty_batch_students(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    current_year: int | None = Query(
        default=None,
        ge=1,
        le=8,
    ),
    is_active: bool | None = Query(
        default=None,
    ),
    search: str | None = Query(
        default=None,
        max_length=120,
    ),
    cursor: int | None = Query(
        default=None,
        ge=1,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=200,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchStudentPage:
    """
    Return one bounded, cursor-paginated page of Students.

    Authorization is derived entirely from the authenticated
    Website Faculty scope.

    College Coordinator:
        authenticated college.

    HOD:
        authenticated college + authenticated department.

    The client cannot provide or override college/department scope.

    The full result set remains traversable through next_cursor,
    while each individual request is limited to at most 200 rows.
    """
    try:
        return await list_website_batch_students(
            db=db,
            scope=scope,
            batch_id=batch_id,
            current_year=current_year,
            is_active=is_active,
            search=search,
            cursor=cursor,
            limit=limit,
        )

    except ValueError as error:
        message = str(error)

        if message == (
            "Academic batch not found within authenticated college"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error


@router.get(
    "/batches/{batch_id}/promotion-preview",
    response_model=WebsiteBatchPromotionPreview,
    summary="Preview an entire-year promotion",
)
async def preview_website_faculty_batch_year_promotion(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    from_year: int = Query(
        ...,
        ge=1,
        le=8,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchPromotionPreview:
    """
    Return exact Student counts for the promotion confirmation
    screen without modifying any Student records.
    """
    try:
        return await preview_website_batch_year_promotion(
            db=db,
            scope=scope,
            batch_id=batch_id,
            from_year=from_year,
        )

    except ValueError as error:
        message = str(error)

        if message == (
            "Active academic batch not found within authenticated college"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        if message in {
            "from_year exceeds the academic batch course duration",
            "Final academic year cannot be promoted further",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error


@router.patch(
    "/batches/{batch_id}/promote-year",
    response_model=WebsiteBatchPromotionResponse,
    summary="Promote an entire academic year",
)
async def promote_entire_website_faculty_batch_year(
    payload: WebsiteBatchPromoteYearRequest,
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchPromotionResponse:
    """
    Promote every eligible active Student in the requested
    batch/year inside the authenticated Website Faculty scope.

    The frontend does not supply Student IDs.

    HOD:
        only the authenticated department.

    College Coordinator:
        the authenticated college.

    Inactive Students remain unchanged.

    The service processes Students in bounded internal chunks while
    this route owns one logical transaction. Student year updates and
    StudentAcademicHistory inserts are committed together.
    """
    try:
        response = (
            await promote_entire_website_batch_year(
                db=db,
                scope=scope,
                batch_id=batch_id,
                payload=payload,
            )
        )

        await db.commit()

        return response

    except ValueError as error:
        await db.rollback()

        message = str(error)

        if message == (
            "Active academic batch not found within authenticated college"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        if message in {
            "from_year exceeds the academic batch course duration",
            "to_year exceeds the academic batch course duration",
            (
                "Batch promotion must move students exactly "
                "one academic year forward"
            ),
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/batches/{batch_id}/promote-selected",
    response_model=WebsiteBatchPromotionResponse,
    summary="Promote selected students to the next academic year",
)
async def promote_selected_website_faculty_batch_students(
    payload: WebsiteBatchPromoteSelectedRequest,
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchPromotionResponse:
    """
    Promote selected active Students exactly one academic year
    forward inside the authenticated Website Faculty scope.

    HOD:
        only Students inside the authenticated department.

    College Coordinator:
        Students inside the authenticated college.

    Students outside scope, in another batch, in another year,
    or otherwise stale are skipped without leaking authorization
    details.

    Inactive scoped Students are counted but never promoted.

    Student updates and StudentAcademicHistory rows are committed
    atomically in one database transaction.
    """
    try:
        response = (
            await promote_selected_website_batch_students(
                db=db,
                scope=scope,
                batch_id=batch_id,
                payload=payload,
            )
        )

        await db.commit()

        return response

    except ValueError as error:
        await db.rollback()

        message = str(error)

        if message == (
            "Active academic batch not found within authenticated college"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        if message in {
            "from_year exceeds the academic batch course duration",
            "to_year exceeds the academic batch course duration",
            (
                "Batch promotion must move students exactly "
                "one academic year forward"
            ),
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/batches/students/move-year",
    response_model=WebsiteStudentMoveYearResponse,
    summary="Manually move a scoped student to another academic year",
)
async def move_website_faculty_student_year(
    payload: WebsiteStudentMoveYearRequest,
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteStudentMoveYearResponse:
    """
    Promote, demote, or correct one Student's academic year.

    Student lookup is by exact USN or email.

    Authorization is derived exclusively from the authenticated
    Website Faculty scope.

    Only Student.current_year may change through this endpoint.
    Department, batch, college, and assigned Faculty remain unchanged.

    The Student update and StudentAcademicHistory row are committed
    atomically in one database transaction.
    """
    try:
        response = await move_website_student_year(
            db=db,
            scope=scope,
            payload=payload,
        )

        await db.commit()

        return response

    except ValueError as error:
        await db.rollback()

        message = str(error)

        if message == (
            "Student not found within authenticated scope"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student not found",
            ) from error

        if message == (
            "Student identifier is ambiguous within authenticated scope"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Student identifier is ambiguous"
                ),
            ) from error

        if message == (
            "Student is already in the requested academic year"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        if message in {
            "Student has no department assignment",
            "Student has no academic batch assignment",
            "Student has no current academic year",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


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


@router.get(
    "/batches/{batch_id}/graduation-preview",
    response_model=WebsiteBatchGraduationPreview,
)
async def preview_website_faculty_batch_graduation(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchGraduationPreview:
    """
    Preview final-year graduation eligibility within the
    authenticated website Faculty scope.

    READ ONLY. No Student lifecycle state is changed.
    """

    try:
        return await preview_website_batch_graduation(
            db=db,
            scope=scope,
            batch_id=batch_id,
        )

    except ValueError as error:
        detail = str(error)

        if (
            "Active academic batch not found"
            in detail
        ):
            raise HTTPException(
                status_code=404,
                detail="Academic batch not found",
            ) from error

        raise HTTPException(
            status_code=400,
            detail=detail,
        ) from error


@router.patch(
    "/batches/{batch_id}/graduate",
    response_model=WebsiteBatchGraduationResponse,
    summary="Graduate eligible final-year students",
)
async def graduate_website_faculty_batch(
    payload: WebsiteBatchGraduateRequest,
    request: Request,
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchGraduationResponse:
    """
    Graduate eligible final-year Students inside the authenticated
    Website Faculty scope.

    HOD:
        only eligible Students in the authenticated department.

    College Coordinator:
        eligible Students across the authenticated college.

    Eligibility is derived entirely by the backend:
        - exact academic batch
        - final year from batch.course_duration_years
        - lifecycle ACTIVE
        - legacy is_active=True
        - authenticated Faculty scope

    The frontend cannot provide Student IDs, college or department.

    Only Student.lifecycle_status and graduated_at are changed.
    Academic assignment, current year, Faculty assignment, points,
    certificates and legacy is_active remain unchanged.

    The Student transitions and immutable AuditLog row are committed
    atomically in one database transaction.
    """

    try:
        response = await graduate_website_batch_final_year(
            db=db,
            scope=scope,
            batch_id=batch_id,
            payload=payload,
            request=request,
        )

        await db.commit()

        return response

    except ValueError as error:
        await db.rollback()

        message = str(error)

        if message == (
            "Active academic batch not found within authenticated college"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        if message == (
            "Academic batch has invalid course duration"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        if message == (
            "Unsupported website Faculty role for graduation"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Faculty role is not permitted "
                    "to graduate students"
                ),
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from error

    except RuntimeError as error:
        await db.rollback()

        message = str(error)

        if message == (
            "Graduation eligibility changed during processing; retry"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from error

        raise

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/batches/{batch_id}/archive-preview",
    response_model=WebsiteBatchArchivePreview,
    summary="Preview graduated students eligible for archive",
)
async def preview_website_faculty_batch_archive(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchArchivePreview:
    """
    Preview GRADUATED -> ARCHIVED eligibility inside the
    authenticated Website Faculty scope.

    READ ONLY.

    No Student lifecycle state or operational data is modified.
    """

    try:
        return await preview_website_batch_archive(
            db=db,
            scope=scope,
            batch_id=batch_id,
        )

    except ValueError as error:
        detail = str(
            error
        )

        if (
            "Active academic batch not found"
            in detail
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error




@router.patch(
    "/batches/{batch_id}/archive",
    response_model=WebsiteBatchArchiveResponse,
    summary="Archive graduated students after verified export",
)
async def archive_website_faculty_batch(
    payload: WebsiteBatchArchiveRequest,
    request: Request,
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchArchiveResponse:
    """
    Transition GRADUATED -> ARCHIVED only after an exact-scope
    verified final export exists.

    No Student row or operational record is deleted.
    """

    try:
        result = await archive_website_batch_graduated_students(
            db=db,
            scope=scope,
            batch_id=batch_id,
            payload=payload,
            request=request,
        )

        await db.commit()

        return result

    except ValueError as error:
        await db.rollback()

        detail = str(
            error
        )

        if (
            "Active academic batch not found"
            in detail
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error

    except RuntimeError as error:
        await db.rollback()

        detail = str(
            error
        )

        conflict_messages = (
            "completed verified final export is required",
            "Student set changed after verified export",
            "Graduated student set changed after verified export",
            "Archive eligibility changed during processing",
        )

        if any(
            message.lower()
            in detail.lower()
            for message in conflict_messages
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            ) from error

        raise

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


# =========================================================
# BATCH FINAL EXPORT
# =========================================================


@router.get(
    "/batches/{batch_id}/exports",
    response_model=WebsiteBatchExportJobResponse | None,
    summary="Get latest batch export job",
)
async def get_latest_website_batch_export(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Restore the newest exact-scope batch export after
    website refresh.

    READ ONLY.
    """

    try:
        job = await get_latest_batch_export_job_within_scope(
            db=db,
            scope=scope,
            batch_id=batch_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if job is None:
        return None

    return _website_batch_export_response(
        job
    )


@router.post(
    "/batches/{batch_id}/exports",
    response_model=WebsiteBatchExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create final batch export job",
)
async def create_website_batch_export(
    batch_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Queue one final six-CSV ZIP export.

    Scope is derived entirely from authentication.
    """

    try:
        job = await create_batch_export_job(
            db=db,
            scope=scope,
            batch_id=batch_id,
        )

        job_id = int(job.id)

        await db.commit()

    except ValueError as exc:
        await db.rollback()

        message = str(exc)

        if (
            "already pending"
            in message.lower()
            or "already processing"
            in message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        ) from exc

    except Exception:
        await db.rollback()
        raise

    try:
        # Local import prevents route/task circular imports.
        from app.workers.tasks import (
            process_batch_export_job_task,
        )

        process_batch_export_job_task.delay(
            export_job_id=job_id
        )

    except Exception as exc:
        # Job was committed already; persist enqueue failure.
        persisted = await db.get(
            BatchExportJob,
            job_id,
        )

        if persisted is not None:
            persisted.status = "FAILED"
            persisted.failure_reason = (
                "Failed to enqueue export worker"
            )
            persisted.verified_at = None
            persisted.completed_at = None

            await db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue batch export",
        ) from exc

    persisted = await db.get(
        BatchExportJob,
        job_id,
    )

    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Batch export job could not be reloaded",
        )

    return _website_batch_export_response(
        persisted
    )


@router.get(
    "/batches/{batch_id}/exports/{export_job_id}",
    response_model=WebsiteBatchExportJobResponse,
    summary="Get batch export status",
)
async def get_website_batch_export_status(
    batch_id: int = Path(..., ge=1),
    export_job_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    job = await get_batch_export_job_within_scope(
        db=db,
        scope=scope,
        batch_id=batch_id,
        export_job_id=export_job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch export job not found",
        )

    return _website_batch_export_response(
        job
    )


@router.get(
    "/batches/{batch_id}/exports/{export_job_id}/download-url",
    response_model=WebsiteBatchExportDownloadResponse,
    summary="Get verified batch export download URL",
)
async def get_website_batch_export_download_url(
    batch_id: int = Path(..., ge=1),
    export_job_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    job = await get_batch_export_job_within_scope(
        db=db,
        scope=scope,
        batch_id=batch_id,
        export_job_id=export_job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch export job not found",
        )

    if (
        str(job.status) != "COMPLETED"
        or job.verified_at is None
        or not job.bucket
        or not job.object_key
        or not job.sha256
        or job.file_size_bytes is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Batch export is not completed and verified"
            ),
        )

    expires = 600

    try:
        url = get_presigned_url(
            bucket=str(job.bucket),
            object_name=str(
                job.object_key
            ),
            expiry_seconds=expires,
            public=False,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Unable to create export download URL"
            ),
        ) from exc

    return WebsiteBatchExportDownloadResponse(
        export_job_id=int(job.id),
        filename=(
            f"batch-{int(job.batch_id)}-"
            f"export-{int(job.id)}.zip"
        ),
        url=url,
        expires_in_seconds=expires,
        sha256=str(job.sha256),
        file_size_bytes=int(
            job.file_size_bytes
        ),
    )


# =========================================================
# BATCH PURGE PREVIEW
# =========================================================


@router.get(
    "/batches/{batch_id}/purge-preview",
    response_model=WebsiteBatchPurgePreview,
    summary="Preview archived batch data eligible for purge",
)
async def preview_website_faculty_batch_purge(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
) -> WebsiteBatchPurgePreview:
    """
    READ ONLY.

    Preview ARCHIVED -> PURGED for the authenticated
    Coordinator/HOD scope.

    No Student lifecycle state, PostgreSQL row, object-storage
    object, certificate, event submission, academic history,
    export job or audit log is modified.
    """

    try:
        return await preview_website_batch_purge(
            db=db,
            scope=scope,
            batch_id=batch_id,
        )

    except ValueError as error:
        detail = str(
            error
        )

        if (
            "Active academic batch not found"
            in detail
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from error

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error



# =========================================================
# BATCH PURGE JOB CONTROL PLANE
# =========================================================


@router.get(
    "/batches/{batch_id}/purges",
    response_model=WebsiteBatchPurgeJobResponse | None,
    summary="Get latest batch purge job",
)
async def get_latest_website_batch_purge(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Restore the latest useful exact-scope purge job.

    READ ONLY.
    """

    try:
        job = (
            await get_latest_batch_purge_job_within_scope(
                db=db,
                scope=scope,
                batch_id=batch_id,
            )
        )

    except ValueError as exc:
        message = str(exc)

        if (
            "only to College Coordinator or HOD"
            in message
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        ) from exc

    if job is None:
        return None

    return _website_batch_purge_response(
        job
    )


@router.post(
    "/batches/{batch_id}/purges",
    response_model=WebsiteBatchPurgeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create durable batch purge job",
)
async def create_website_batch_purge(
    payload: WebsiteBatchPurgeRequest,
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    """
    Create durable PENDING purge metadata and enqueue
    the purge worker only after the job transaction commits.

    The HTTP request itself performs no physical purge.
    """

    try:
        job = await create_batch_purge_job(
            db=db,
            scope=scope,
            batch_id=batch_id,
            reason=payload.reason,
        )

        job_id = int(
            job.id
        )

        await db.commit()

    except ValueError as exc:
        await db.rollback()

        message = str(
            exc
        )

        if (
            "only to College Coordinator or HOD"
            in message
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message,
            ) from exc

        if (
            "Active academic batch not found"
            in message
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic batch not found",
            ) from exc

        conflict_fragments = (
            "already pending",
            "already processing",
            "No archived students",
            "verified final export",
            "Student set changed",
            "Unsafe storage references",
            "not ready for purge",
            "Verified export evidence",
            "scope mismatch",
        )

        if any(
            fragment.lower()
            in message.lower()
            for fragment in conflict_fragments
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc

    except Exception:
        await db.rollback()
        raise

    try:
        # Local import avoids route/task circular imports.
        from app.workers.tasks import (
            process_batch_purge_job_task,
        )

        process_batch_purge_job_task.delay(
            purge_job_id=job_id
        )

    except Exception as exc:
        # The durable job already committed. Release the active
        # unique-scope guard by marking enqueue failure FAILED.
        persisted = await db.get(
            BatchPurgeJob,
            job_id,
        )

        if persisted is not None:
            persisted.status = "FAILED"
            persisted.failure_reason = (
                "Failed to enqueue purge worker"
            )
            persisted.completed_at = None

            await db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue batch purge",
        ) from exc

    persisted = await db.get(
        BatchPurgeJob,
        job_id,
    )

    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Batch purge job could not be reloaded"
            ),
        )

    return _website_batch_purge_response(
        persisted
    )


@router.get(
    "/batches/{batch_id}/purges/{purge_job_id}",
    response_model=WebsiteBatchPurgeJobResponse,
    summary="Get batch purge job status",
)
async def get_website_batch_purge_status(
    batch_id: int = Path(
        ...,
        ge=1,
    ),
    purge_job_id: int = Path(
        ...,
        ge=1,
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):
    try:
        job = await get_batch_purge_job_within_scope(
            db=db,
            scope=scope,
            batch_id=batch_id,
            purge_job_id=purge_job_id,
        )

    except ValueError as exc:
        message = str(
            exc
        )

        if (
            "only to College Coordinator or HOD"
            in message
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        ) from exc

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch purge job not found",
        )

    return _website_batch_purge_response(
        job
    )
