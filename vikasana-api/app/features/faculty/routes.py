from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, desc, delete
from sqlalchemy.orm import selectinload
import csv
import io

from app.core.database import get_db
from app.core.dependencies import get_current_admin, get_current_faculty

from app.features.auth.models import Admin
from app.features.faculty.models import Faculty, FacultyActivationSession
from app.features.students.models import Student
from app.features.organization.models import College, CollegeAlias
from app.features.organization.service import (
    _college_scope_names,
    get_organization_settings,
)

from app.features.activities.models import ActivitySession, ActivitySessionStatus

from app.features.faculty.schemas.faculty import (
    FacultyCreateResponse,
    FacultyResponse,
    ActivateFacultyResponse,
    FacultyCreateRequest,
    FacultyUpdateRequest,
)

from app.features.faculty.schemas.faculty_import import FacultyImportResponse, FailedRow

from app.features.faculty.schemas.faculty_activation import (
    ActivationValidateResponse,
    SendOtpRequest,
    VerifyOtpRequest,
    VerifyOtpResponse,
    SetPasswordRequest,
    SetPasswordResponse,
)

from app.features.faculty.service import (
    create_faculty,
    validate_activation_token_and_create_session,
    send_activation_otp,
    verify_activation_otp,
    set_password_after_otp,
    activate_faculty,
    update_faculty,
    resend_faculty_activation,
    add_faculty_access_history,
)

from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_FACULTY,
    ROLE_HOD,
    normalize_faculty_role,
)

from pydantic import BaseModel

router = APIRouter(prefix="/faculty", tags=["Faculty"])


# =========================================================
# ADMIN ONLY: CREATE / LIST / DELETE FACULTY
# =========================================================

@router.post("", response_model=FacultyCreateResponse, summary="Create Faculty (Admin only)")
async def add_faculty(
    full_name: str = Form(...),
    college: str = Form(...),
    email: str = Form(...),
    role: str = Form("faculty"),
    department_id: int | None = Form(None),
    parent_faculty_id: int | None = Form(None),
    image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    payload = FacultyCreateRequest(
        full_name=full_name,
        college=college,
        email=email,
        role=role,
        department_id=department_id,
    )

    validated_parent_faculty_id: int | None = None

    # -----------------------------------------------------
    # Admin -> Faculty/Mentor hierarchy enforcement
    # -----------------------------------------------------
    if payload.role == ROLE_FACULTY:
        normalized_college = payload.college.strip().casefold()

        canonical_result = await db.execute(
            select(College).where(
                func.lower(func.trim(College.name))
                == normalized_college
            )
        )

        canonical_college = (
            canonical_result.scalar_one_or_none()
        )

        if canonical_college is None:
            alias_result = await db.execute(
                select(College)
                .join(
                    CollegeAlias,
                    CollegeAlias.college_id == College.id,
                )
                .where(
                    CollegeAlias.is_active.is_(True),
                    func.lower(func.trim(CollegeAlias.alias))
                    == normalized_college,
                )
            )

            canonical_college = (
                alias_result.scalars().first()
            )

        if canonical_college is not None:
            accepted_college_names = await _college_scope_names(
                db,
                canonical_college,
            )

            # New Admin-created hierarchy records use the canonical
            # college name. Historical Faculty records and aliases
            # remain untouched.
            payload = payload.model_copy(
                update={
                    "college": canonical_college.name,
                }
            )

            settings_college = canonical_college.name
        else:
            accepted_college_names = [
                normalized_college
            ]
            settings_college = payload.college

        organization_settings = await get_organization_settings(
            db,
            settings_college,
        )

        if organization_settings.department_architecture_enabled:
            if payload.department_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "department_id is required when department "
                        "architecture is enabled"
                    ),
                )

            if parent_faculty_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "parent_faculty_id is required for "
                        "Faculty/Mentor creation when department "
                        "architecture is enabled"
                    ),
                )

        if parent_faculty_id is not None:
            parent_result = await db.execute(
                select(Faculty).where(
                    Faculty.id == parent_faculty_id,
                    Faculty.is_active.is_(True),
                )
            )

            parent_faculty = (
                parent_result.scalar_one_or_none()
            )

            if parent_faculty is None:
                raise HTTPException(
                    status_code=400,
                    detail="Selected HOD was not found or is inactive",
                )

            try:
                parent_role = normalize_faculty_role(
                    parent_faculty.role
                )
            except ValueError as error:
                raise HTTPException(
                    status_code=400,
                    detail="Selected parent has an invalid Faculty role",
                ) from error

            if parent_role != ROLE_HOD:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Faculty/Mentor parent must be an HOD"
                    ),
                )

            parent_college_key = (
                str(parent_faculty.college)
                .strip()
                .casefold()
            )

            if parent_college_key not in accepted_college_names:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Selected HOD must belong to the same college"
                    ),
                )

            if payload.department_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "department_id is required when assigning "
                        "an HOD parent"
                    ),
                )

            if (
                parent_faculty.department_id
                != payload.department_id
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Selected HOD must belong to the same "
                        "department as the Faculty/Mentor"
                    ),
                )

            validated_parent_faculty_id = parent_faculty.id

    elif payload.role == ROLE_HOD:
        if payload.department_id is None:
            raise HTTPException(
                status_code=400,
                detail="department_id is required for HOD creation",
            )

        if parent_faculty_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "parent_faculty_id is required for HOD creation "
                    "and must reference the College Coordinator"
                ),
            )

        normalized_college = payload.college.strip().casefold()

        canonical_result = await db.execute(
            select(College).where(
                func.lower(func.trim(College.name))
                == normalized_college
            )
        )

        canonical_college = (
            canonical_result.scalar_one_or_none()
        )

        if canonical_college is None:
            alias_result = await db.execute(
                select(College)
                .join(
                    CollegeAlias,
                    CollegeAlias.college_id == College.id,
                )
                .where(
                    CollegeAlias.is_active.is_(True),
                    func.lower(func.trim(CollegeAlias.alias))
                    == normalized_college,
                )
            )

            canonical_college = (
                alias_result.scalars().first()
            )

        if canonical_college is not None:
            accepted_college_names = await _college_scope_names(
                db,
                canonical_college,
            )

            payload = payload.model_copy(
                update={
                    "college": canonical_college.name,
                }
            )
        else:
            accepted_college_names = [
                normalized_college
            ]

        parent_result = await db.execute(
            select(Faculty).where(
                Faculty.id == parent_faculty_id,
                Faculty.is_active.is_(True),
            )
        )

        parent_faculty = (
            parent_result.scalar_one_or_none()
        )

        if parent_faculty is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected College Coordinator was not found "
                    "or is inactive"
                ),
            )

        try:
            parent_role = normalize_faculty_role(
                parent_faculty.role
            )
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected parent has an invalid Faculty role"
                ),
            ) from error

        if parent_role != ROLE_COLLEGE_COORDINATOR:
            raise HTTPException(
                status_code=400,
                detail=(
                    "HOD parent must be a College Coordinator"
                ),
            )

        parent_college_key = (
            str(parent_faculty.college)
            .strip()
            .casefold()
        )

        if parent_college_key not in accepted_college_names:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected College Coordinator must belong "
                    "to the same college"
                ),
            )

        validated_parent_faculty_id = parent_faculty.id

    elif parent_faculty_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "parent_faculty_id is supported only for "
                "HOD and Faculty/Mentor hierarchy creation"
            ),
        )

    image_bytes = None
    if image:
        image_bytes = await image.read()

    faculty, email_sent = await create_faculty(
        payload=payload,
        db=db,
        image_bytes=image_bytes,
        image_content_type=image.content_type if image else None,
        image_filename=image.filename if image else None,
        parent_faculty_id=validated_parent_faculty_id,
        created_by_admin_id=admin.id,
    )

    message = (
        "Faculty created and activation email sent."
        if email_sent
        else "Faculty created, but activation email could not be sent (email not configured)."
    )

    return {
        "faculty": FacultyResponse.model_validate(faculty),
        "activation_email_sent": email_sent,
        "message": message,
    }


@router.post("/import-csv", response_model=FacultyImportResponse, summary="Import Faculty via CSV (Admin only)")
async def import_faculty_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except Exception:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header row")

    required = {"full_name", "email", "college", "role"}
    headers = {h.strip() for h in reader.fieldnames if h}
    missing = required - headers
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(sorted(missing))}")

    created_faculty: list[FacultyResponse] = []
    failed_rows: list[FailedRow] = []
    created_count = 0
    email_sent_count = 0
    row_number = 1

    for row in reader:
        row_number += 1
        try:
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip().lower()
            college = (row.get("college") or "").strip()
            role = (row.get("role") or "faculty").strip() or "faculty"

            raw_department_id = (row.get("department_id") or "").strip()
            department_id: int | None = None

            if raw_department_id:
                try:
                    department_id = int(raw_department_id)
                except ValueError:
                    raise ValueError("department_id must be a positive integer")

                if department_id <= 0:
                    raise ValueError("department_id must be a positive integer")

            raw_parent_faculty_id = (
                row.get("parent_faculty_id") or ""
            ).strip()
            parent_faculty_id: int | None = None

            if raw_parent_faculty_id:
                try:
                    parent_faculty_id = int(raw_parent_faculty_id)
                except ValueError:
                    raise ValueError(
                        "parent_faculty_id must be a positive integer"
                    )

                if parent_faculty_id <= 0:
                    raise ValueError(
                        "parent_faculty_id must be a positive integer"
                    )

            if not full_name:
                raise ValueError("full_name is required")
            if not email or "@" not in email:
                raise ValueError("valid email is required")
            if not college:
                raise ValueError("college is required")

            existing = await db.execute(select(Faculty).where(Faculty.email == email))
            if existing.scalar_one_or_none():
                raise ValueError("email already exists")

            payload = FacultyCreateRequest(
                full_name=full_name,
                college=college,
                email=email,
                role=role,
                department_id=department_id,
            )

            validated_parent_faculty_id: int | None = None

            if payload.role == ROLE_FACULTY:
                normalized_college = (
                    payload.college.strip().casefold()
                )

                canonical_result = await db.execute(
                    select(College).where(
                        func.lower(func.trim(College.name))
                        == normalized_college
                    )
                )
                canonical_college = (
                    canonical_result.scalar_one_or_none()
                )

                if canonical_college is None:
                    alias_result = await db.execute(
                        select(College)
                        .join(
                            CollegeAlias,
                            CollegeAlias.college_id == College.id,
                        )
                        .where(
                            CollegeAlias.is_active.is_(True),
                            func.lower(
                                func.trim(CollegeAlias.alias)
                            ) == normalized_college,
                        )
                    )
                    canonical_college = (
                        alias_result.scalars().first()
                    )

                if canonical_college is not None:
                    accepted_college_names = (
                        await _college_scope_names(
                            db,
                            canonical_college,
                        )
                    )

                    payload = payload.model_copy(
                        update={
                            "college": canonical_college.name,
                        }
                    )

                    settings_college = canonical_college.name
                else:
                    accepted_college_names = [
                        normalized_college
                    ]
                    settings_college = payload.college

                organization_settings = (
                    await get_organization_settings(
                        db,
                        settings_college,
                    )
                )

                if (
                    organization_settings
                    .department_architecture_enabled
                ):
                    if payload.department_id is None:
                        raise ValueError(
                            "department_id is required when "
                            "department architecture is enabled"
                        )

                    if parent_faculty_id is None:
                        raise ValueError(
                            "parent_faculty_id is required for "
                            "Faculty/Mentor creation when department "
                            "architecture is enabled"
                        )

                if parent_faculty_id is not None:
                    parent_result = await db.execute(
                        select(Faculty).where(
                            Faculty.id == parent_faculty_id,
                            Faculty.is_active.is_(True),
                        )
                    )

                    parent_faculty = (
                        parent_result.scalar_one_or_none()
                    )

                    if parent_faculty is None:
                        raise ValueError(
                            "Selected HOD was not found or is inactive"
                        )

                    try:
                        parent_role = normalize_faculty_role(
                            parent_faculty.role
                        )
                    except ValueError as error:
                        raise ValueError(
                            "Selected parent has an invalid Faculty role"
                        ) from error

                    if parent_role != ROLE_HOD:
                        raise ValueError(
                            "Faculty/Mentor parent must be an HOD"
                        )

                    parent_college_key = (
                        str(parent_faculty.college)
                        .strip()
                        .casefold()
                    )

                    if (
                        parent_college_key
                        not in accepted_college_names
                    ):
                        raise ValueError(
                            "Selected HOD must belong to the same college"
                        )

                    if payload.department_id is None:
                        raise ValueError(
                            "department_id is required when "
                            "assigning an HOD parent"
                        )

                    if (
                        parent_faculty.department_id
                        != payload.department_id
                    ):
                        raise ValueError(
                            "Selected HOD must belong to the same "
                            "department as the Faculty/Mentor"
                        )

                    validated_parent_faculty_id = (
                        parent_faculty.id
                    )

            elif parent_faculty_id is not None:
                raise ValueError(
                    "parent_faculty_id is supported only for "
                    "Faculty/Mentor creation"
                )

            faculty, email_sent = await create_faculty(
                payload=payload,
                db=db,
                image_bytes=None,
                image_content_type=None,
                image_filename=None,
                parent_faculty_id=validated_parent_faculty_id,
                created_by_admin_id=admin.id,
            )

            created_faculty.append(FacultyResponse.model_validate(faculty))
            created_count += 1
            if email_sent:
                email_sent_count += 1

        except Exception as e:
            failed_rows.append(FailedRow(row_number=row_number, error=str(e)))

    return FacultyImportResponse(
        created_count=created_count,
        failed_count=len(failed_rows),
        activation_email_sent_count=email_sent_count,
        failed_rows=failed_rows,
        created_faculty=created_faculty,
    )


@router.get("", response_model=list[FacultyResponse], summary="List faculty (Admin only)")
async def list_faculty(
    college_id: int | None = Query(
        None,
        ge=1,
        description="Optional canonical college ID. Includes active aliases.",
    ),
    role: str | None = Query(
        None,
        description="Optional faculty role filter.",
    ),
    parent_faculty_id: int | None = Query(
        None,
        ge=1,
        description="Optional parent faculty/HOD ID filter.",
    ),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    stmt = select(Faculty)

    # ---------------------------------------------------------
    # Canonical college scope.
    #
    # When college_id is supplied, match the canonical college
    # name plus all active historical aliases.
    #
    # Existing Faculty.college values remain untouched.
    # ---------------------------------------------------------

    if college_id is not None:
        college_result = await db.execute(
            select(College).where(
                College.id == college_id
            )
        )

        canonical_college = (
            college_result.scalar_one_or_none()
        )

        if canonical_college is None:
            raise HTTPException(
                status_code=404,
                detail="College not found",
            )

        alias_result = await db.execute(
            select(CollegeAlias.alias).where(
                CollegeAlias.college_id == college_id,
                CollegeAlias.is_active.is_(True),
            )
        )

        aliases = alias_result.scalars().all()

        accepted_names = [
            canonical_college.name,
            *aliases,
        ]

        normalized_names = [
            name.strip().lower()
            for name in accepted_names
            if name and name.strip()
        ]

        stmt = stmt.where(
            func.lower(
                func.trim(Faculty.college)
            ).in_(normalized_names)
        )

    # ---------------------------------------------------------
    # Optional canonical role filter.
    # ---------------------------------------------------------

    if role is not None and role.strip():
        normalized_role = normalize_faculty_role(
            role
        )

        stmt = stmt.where(
            func.lower(
                func.trim(Faculty.role)
            ) == normalized_role
        )

        # College Coordinator hierarchy views represent the current
        # assignment, not every historical Coordinator row.
        #
        # Keep:
        # - active Coordinator accounts
        # - pending-activation Coordinator accounts
        #
        # Hide:
        # - historical Coordinators explicitly removed by Admin
        if normalized_role == ROLE_COLLEGE_COORDINATOR:
            stmt = stmt.where(
                or_(
                    Faculty.is_active.is_(True),
                    Faculty.activation_token_hash.is_not(None),
                )
            )

    if parent_faculty_id is not None:
        stmt = stmt.where(
            Faculty.parent_faculty_id == parent_faculty_id
        )

    stmt = stmt.order_by(
        Faculty.created_at.desc()
    )

    result = await db.execute(stmt)

    items = result.scalars().all()

    return [
        FacultyResponse.model_validate(item)
        for item in items
    ]


@router.post(
    "/{faculty_id}/resend-activation",
    summary="Resend account activation invitation (Admin only)",
)
async def resend_activation_invitation(
    faculty_id: int,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    faculty, activation_email_sent = await resend_faculty_activation(
        faculty_id=faculty_id,
        db=db,
    )

    return {
        "detail": (
            "Activation invitation resent successfully"
            if activation_email_sent
            else "Activation invitation regenerated, but email delivery failed"
        ),
        "faculty_id": faculty.id,
        "email": faculty.email,
        "role": faculty.role,
        "activation_email_sent": activation_email_sent,
        "activation_expires_at": faculty.activation_expires_at,
    }


@router.patch(
    "/{faculty_id}",
    response_model=FacultyResponse,
    summary="Update faculty member (Admin only)",
)
async def patch_faculty(
    faculty_id: int,
    body: FacultyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    faculty = await update_faculty(
        faculty_id=faculty_id,
        payload=body,
        db=db,
    )
    return FacultyResponse.model_validate(faculty)


@router.delete(
    "/{faculty_id}",
    summary="Deactivate faculty member (Admin only)",
)
async def delete_faculty(
    faculty_id: int,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """
    Non-destructive Faculty removal.

    Historical Faculty rows are never physically deleted.

    Removing an account:
    - marks it inactive,
    - revokes any pending activation invitation,
    - invalidates outstanding OTP activation sessions,
    - preserves password/hierarchy/provenance,
    - records an immutable access-history entry.
    """

    result = await db.execute(
        select(Faculty).where(
            Faculty.id == faculty_id
        )
    )

    faculty = result.scalar_one_or_none()

    if not faculty:
        raise HTTPException(
            status_code=404,
            detail="Faculty not found",
        )

    was_active = bool(faculty.is_active)
    had_pending_activation = bool(
        faculty.activation_token_hash
    )

    # -----------------------------------------------------
    # Soft deactivate.
    # -----------------------------------------------------

    faculty.is_active = False

    # A removed pending account must never be able to use
    # an old invitation link to reactivate itself.
    faculty.activation_token_hash = None
    faculty.activation_expires_at = None

    # Invalidate every OTP / set-password activation session.
    await db.execute(
        delete(FacultyActivationSession).where(
            FacultyActivationSession.faculty_id
            == faculty.id
        )
    )

    changed = (
        was_active
        or had_pending_activation
    )

    if changed:
        await add_faculty_access_history(
            db=db,
            faculty_id=faculty.id,
            action="deactivated",
            college=faculty.college,
            previous_role=faculty.role,
            new_role=faculty.role,
            previous_parent_faculty_id=(
                faculty.parent_faculty_id
            ),
            new_parent_faculty_id=(
                faculty.parent_faculty_id
            ),
            department_id=faculty.department_id,
            changed_by_admin_id=admin.id,
            note=(
                "Faculty account deactivated; "
                "pending activation access revoked"
            ),
        )

    try:
        await db.commit()
        await db.refresh(faculty)
    except Exception:
        await db.rollback()
        raise

    return {
        "detail": (
            f"Faculty {faculty_id} deactivated"
            if changed
            else f"Faculty {faculty_id} is already inactive"
        ),
        "faculty_id": faculty.id,
        "is_active": faculty.is_active,
        "activation_revoked": True,
    }


# =========================================================
# FACULTY APP: DASHBOARD STATS
# GET /api/faculty/dashboard/stats
# =========================================================

@router.get("/dashboard/stats", summary="Faculty dashboard stats (Faculty auth)")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    students = await db.scalar(
        select(func.count())
        .select_from(Student)
        .where(Student.college == current_faculty.college)
    )

    return {
        "students": int(students or 0),
        "verified": 0,
        "pending": 0,
        "rejected": 0,
    }


# =========================================================
# FACULTY APP: LIST ACTIVITY SESSIONS
# =========================================================

@router.get("/activity-sessions", summary="List activity sessions (Faculty auth)")
async def list_activity_sessions(
    q: str | None = Query(None, description="Search by student name/usn/activity"),
    status: str | None = Query(None, description="DRAFT/SUBMITTED/APPROVED/REJECTED/FLAGGED/EXPIRED"),
    limit: int | None = Query(None, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    stmt = (
        select(ActivitySession)
        .options(selectinload(ActivitySession.student))
        .where(ActivitySession.student.has(Student.college == current_faculty.college))
        .order_by(desc(ActivitySession.created_at))
    )

    if q:
        qq = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            ActivitySession.student.has(
                or_(
                    func.lower(Student.name).like(qq),
                    func.lower(Student.usn).like(qq),
                )
            )
        )

    if status:
        s = status.strip().upper()
        if s in ActivitySessionStatus.__members__:
            stmt = stmt.where(ActivitySession.status == ActivitySessionStatus[s])

    if limit:
        stmt = stmt.limit(limit)

    if offset:
        stmt = stmt.offset(offset)

    sessions = (await db.execute(stmt)).scalars().all()

    activities = []
    for sess in sessions:
        stu = sess.student
        activities.append(
            {
                "id": sess.id,
                "title": sess.activity_name,
                "student_name": stu.name if stu else "—",
                "usn": stu.usn if stu else "—",
                "category": None,
                "description": sess.description,
                "status": (
                    sess.status.value
                    if hasattr(sess.status, "value")
                    else str(sess.status)
                ).lower(),
                "submitted_at": sess.submitted_at or sess.created_at,
            }
        )

    return {"activities": activities, "count": len(activities)}


# =========================================================
# FACULTY APP: UPDATE STATUS
# =========================================================

@router.patch("/activity-sessions/{session_id}/status", summary="Update activity session status (Faculty auth)")
async def update_activity_session_status(
    session_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    raw = (body.get("status") or "").strip().upper()

    if raw in ("APPROVED", "REJECTED", "SUBMITTED", "FLAGGED", "DRAFT", "EXPIRED"):
        new_status = ActivitySessionStatus[raw]
    elif raw.lower() in ("approved", "rejected"):
        new_status = ActivitySessionStatus.APPROVED if raw.lower() == "approved" else ActivitySessionStatus.REJECTED
    else:
        raise HTTPException(status_code=400, detail="status must be approved or rejected")

    q = await db.execute(
        select(ActivitySession)
        .options(selectinload(ActivitySession.student))
        .where(ActivitySession.id == session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activity session not found")

    if not sess.student or (sess.student.college or "") != (current_faculty.college or ""):
        raise HTTPException(status_code=403, detail="Not allowed")

    sess.status = new_status
    await db.commit()
    await db.refresh(sess)

    return {"detail": "Status updated", "id": sess.id, "status": sess.status.value.lower()}


# =========================================================
# ACTIVATION FLOW
# =========================================================

@router.get("/activation/validate", response_model=ActivationValidateResponse, summary="Validate activation token and create activation session")
async def activation_validate(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    session_id, email_masked, expires_at = await validate_activation_token_and_create_session(token, db)
    return {"activation_session_id": session_id, "email_masked": email_masked, "expires_at": expires_at}


@router.post("/activation/send-otp", summary="Send OTP to faculty email")
async def activation_send_otp(
    body: SendOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    await send_activation_otp(body.activation_session_id, db)
    return {"detail": "OTP sent successfully"}


@router.post("/activation/verify-otp", response_model=VerifyOtpResponse, summary="Verify OTP and return set password token")
async def activation_verify_otp(
    body: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    set_password_token = await verify_activation_otp(body.activation_session_id, body.otp, db)
    return {"set_password_token": set_password_token}


@router.post("/activation/set-password", response_model=SetPasswordResponse, summary="Set password after OTP verification")
async def activation_set_password(
    body: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    await set_password_after_otp(body.set_password_token, body.new_password, db)
    return {"detail": "Password set successfully. Account activated."}


@router.get("/activate", response_model=ActivateFacultyResponse, summary="(OLD) Activate faculty account via email token (no OTP)")
async def activate(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    await activate_faculty(token, db)
    return {"detail": "Account activated successfully."}


class VerifyQrRequest(BaseModel):
    qr: str


@router.post("/verify-qr", summary="Verify QR (Faculty auth)")
async def verify_qr(
    body: VerifyQrRequest,
    db: AsyncSession = Depends(get_db),
    current_faculty: Faculty = Depends(get_current_faculty),
):
    qr = (body.qr or "").strip()
    if not qr:
        raise HTTPException(status_code=400, detail="qr is required")

    return {
        "ok": True,
        "detail": "QR received",
        "qr": qr,
        "faculty_id": current_faculty.id,
        "faculty_college": current_faculty.college,
    }