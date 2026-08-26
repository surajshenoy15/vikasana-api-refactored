from __future__ import annotations

from collections import defaultdict

from sqlalchemy import (
    bindparam,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.student_purge_storage import (
    PurgeStorageObject,
    activity_photo_object,
    deduplicate_purge_objects,
    delete_purge_storage_objects,
    face_enrollment_object,
    processed_face_object,
)
from app.features.audit.service import append_audit_log
from app.features.faculty.permission_scope import (
    FacultyScopeType,
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_HOD,
)
from app.features.faculty.schemas.website_batch_management import (
    WebsiteBatchPurgePreview,
)
from app.features.organization.models import (
    AcademicBatch,
    BatchExportJob,
    BatchPurgeJob,
)
from app.features.students.models import Student


PURGE_PREVIEW_STUDENT_CHUNK_SIZE = 5000


# ============================================================
# HELPERS
# ============================================================


def _normalized_college_key(
    value: str,
) -> str:
    return str(
        value or ""
    ).strip().lower()


async def _resolve_batch(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> AcademicBatch:
    if int(batch_id) < 1:
        raise ValueError(
            "batch_id must be greater than zero"
        )

    college_key = (
        _normalized_college_key(
            scope.college
        )
    )

    batch = (
        await db.execute(
            select(
                AcademicBatch
            )
            .where(
                AcademicBatch.id
                == int(batch_id),

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
    ).scalar_one_or_none()

    if batch is None:
        raise ValueError(
            "Active academic batch not found "
            "within authenticated college"
        )

    return batch


async def _latest_verified_export(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> BatchExportJob | None:
    college_key = (
        _normalized_college_key(
            scope.college
        )
    )

    statement = (
        select(
            BatchExportJob
        )
        .where(
            BatchExportJob.batch_id
            == int(batch_id),

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

    if (
        scope.scope_type.value
        == "department"
    ):
        statement = (
            statement.where(
                BatchExportJob.department_id
                == scope.department_id
            )
        )

    else:
        statement = (
            statement.where(
                BatchExportJob.department_id.is_(
                    None
                )
            )
        )

    return (
        await db.execute(
            statement
        )
    ).scalar_one_or_none()


# ============================================================
# PURGE ROW COUNTS
# ============================================================


_COUNT_QUERY = text(
    """
    SELECT 'activity_sessions' AS kind, COUNT(*)::bigint AS row_count
    FROM activity_sessions
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'activity_photos', COUNT(*)::bigint
    FROM activity_photos
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'activity_face_checks', COUNT(*)::bigint
    FROM activity_face_checks
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'event_submission_photos', COUNT(*)::bigint
    FROM event_submission_photos esp
    JOIN event_submissions es
      ON es.id = esp.submission_id
    WHERE es.student_id IN :student_ids

    UNION ALL

    SELECT 'student_face_embeddings', COUNT(*)::bigint
    FROM student_face_embeddings
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_face_enrollment_images', COUNT(*)::bigint
    FROM student_face_enrollment_images
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_push_devices', COUNT(*)::bigint
    FROM student_push_devices
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_notification_deliveries', COUNT(*)::bigint
    FROM student_notification_deliveries
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'event_role_assignments', COUNT(*)::bigint
    FROM event_role_assignments
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_faculty_assignments', COUNT(*)::bigint
    FROM student_faculty_assignments
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_activity_progress', COUNT(*)::bigint
    FROM student_activity_progress
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_activity_stats', COUNT(*)::bigint
    FROM student_activity_stats
    WHERE student_id IN :student_ids

    UNION ALL

    SELECT 'student_point_adjustments', COUNT(*)::bigint
    FROM student_point_adjustments
    WHERE student_id IN :student_ids
    """
).bindparams(
    bindparam(
        "student_ids",
        expanding=True,
    )
)


async def _count_candidate_rows(
    *,
    db: AsyncSession,
    student_ids: list[int],
) -> dict[str, int]:
    if not student_ids:
        return {}

    result = await db.execute(
        _COUNT_QUERY,
        {
            "student_ids": student_ids,
        },
    )

    return {
        str(row.kind): int(
            row.row_count or 0
        )
        for row in result
    }


# ============================================================
# STORAGE VALIDATION
# ============================================================


_ACTIVITY_STORAGE_QUERY = text(
    """
    SELECT
        student_id,
        image_url
    FROM activity_photos
    WHERE student_id IN :student_ids
    ORDER BY id ASC
    """
).bindparams(
    bindparam(
        "student_ids",
        expanding=True,
    )
)


_EVENT_STORAGE_QUERY = text(
    """
    SELECT
        es.student_id AS student_id,
        esp.image_url AS image_url
    FROM event_submission_photos esp
    JOIN event_submissions es
      ON es.id = esp.submission_id
    WHERE es.student_id IN :student_ids
    ORDER BY esp.id ASC
    """
).bindparams(
    bindparam(
        "student_ids",
        expanding=True,
    )
)


_ENROLLMENT_STORAGE_QUERY = text(
    """
    SELECT
        student_id,
        image_key,
        image_url
    FROM student_face_enrollment_images
    WHERE student_id IN :student_ids
    ORDER BY id ASC
    """
).bindparams(
    bindparam(
        "student_ids",
        expanding=True,
    )
)


_PROCESSED_FACE_STORAGE_QUERY = text(
    """
    SELECT
        session_id,
        photo_id,
        processed_object
    FROM activity_face_checks
    WHERE student_id IN :student_ids
      AND processed_object IS NOT NULL
      AND btrim(processed_object) <> ''
    ORDER BY id ASC
    """
).bindparams(
    bindparam(
        "student_ids",
        expanding=True,
    )
)


async def _validate_storage_for_students(
    *,
    db: AsyncSession,
    student_ids: list[int],
) -> tuple[int, int, int, int]:
    """
    Returns:

        (
            storage_reference_count,
            safe_storage_object_count,
            ignored_storage_metadata_count,
            unsafe_storage_reference_count,
        )

    No object storage request is performed.
    """

    if not student_ids:
        return (
            0,
            0,
            0,
            0,
        )

    reference_count = 0
    safe_count = 0
    ignored_count = 0
    unsafe_count = 0

    # --------------------------------------------------------
    # Activity-session photos
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _ACTIVITY_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        reference_count += 1

        try:
            activity_photo_object(
                image_url=str(
                    row.image_url or ""
                ),
                student_id=int(
                    row.student_id
                ),
            )

            safe_count += 1

        except (
            TypeError,
            ValueError,
        ):
            unsafe_count += 1

    # --------------------------------------------------------
    # Event-submission photos
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _EVENT_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        reference_count += 1

        try:
            activity_photo_object(
                image_url=str(
                    row.image_url or ""
                ),
                student_id=int(
                    row.student_id
                ),
            )

            safe_count += 1

        except (
            TypeError,
            ValueError,
        ):
            unsafe_count += 1

    # --------------------------------------------------------
    # Face enrollment images
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _ENROLLMENT_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        reference_count += 1

        stored_value = (
            row.image_key
            or row.image_url
            or ""
        )

        try:
            face_enrollment_object(
                image_key=str(
                    stored_value
                ),
                student_id=int(
                    row.student_id
                ),
            )

            safe_count += 1

        except (
            TypeError,
            ValueError,
        ):
            unsafe_count += 1

    # --------------------------------------------------------
    # Processed / boxed face images
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _PROCESSED_FACE_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        reference_count += 1

        try:
            item = (
                processed_face_object(
                    processed_object=(
                        row.processed_object
                    ),
                    session_id=int(
                        row.session_id
                    ),
                    photo_id=int(
                        row.photo_id
                    ),
                )
            )

            if item is None:
                ignored_count += 1
            else:
                safe_count += 1

        except (
            TypeError,
            ValueError,
        ):
            unsafe_count += 1

    return (
        reference_count,
        safe_count,
        ignored_count,
        unsafe_count,
    )


# ============================================================
# PUBLIC READ-ONLY PREVIEW
# ============================================================


async def preview_website_batch_purge(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> WebsiteBatchPurgePreview:
    """
    Read-only preview for:

        ARCHIVED -> PURGED

    The preview:
    - uses exact authenticated Coordinator/HOD scope;
    - scans Students with keyset pagination;
    - considers only ARCHIVED Students purge-eligible;
    - counts PostgreSQL rows which the future purge would delete;
    - validates object-storage references without touching storage;
    - confirms an exact-scope verified final export still exists;
    - performs no UPDATE, DELETE, INSERT, COMMIT or storage call.
    """

    batch = await _resolve_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    lifecycle_counts: dict[
        str,
        int,
    ] = defaultdict(
        int
    )

    selected_count = 0

    candidate_counts: dict[
        str,
        int,
    ] = defaultdict(
        int
    )

    storage_reference_count = 0
    safe_storage_object_count = 0
    ignored_storage_metadata_count = 0
    unsafe_storage_reference_count = 0

    cursor = 0

    while True:
        statement = (
            select(
                Student.id,
                Student.lifecycle_status,
            )
            .where(
                Student.batch_id
                == batch.id,

                Student.id
                > cursor,
            )
            .order_by(
                Student.id.asc()
            )
            .limit(
                PURGE_PREVIEW_STUDENT_CHUNK_SIZE
            )
        )

        statement = (
            apply_student_scope_to_statement(
                statement,
                scope,
            )
        )

        rows = (
            await db.execute(
                statement
            )
        ).all()

        if not rows:
            break

        cursor = int(
            rows[-1].id
        )

        archived_ids: list[int] = []

        for row in rows:
            selected_count += 1

            lifecycle = str(
                row.lifecycle_status
                or "ACTIVE"
            ).upper()

            lifecycle_counts[
                lifecycle
            ] += 1

            if lifecycle == "ARCHIVED":
                archived_ids.append(
                    int(
                        row.id
                    )
                )

        if not archived_ids:
            continue

        chunk_counts = (
            await _count_candidate_rows(
                db=db,
                student_ids=archived_ids,
            )
        )

        for (
            key,
            value,
        ) in chunk_counts.items():
            candidate_counts[
                key
            ] += int(
                value
            )

        (
            chunk_references,
            chunk_safe,
            chunk_ignored,
            chunk_unsafe,
        ) = await _validate_storage_for_students(
            db=db,
            student_ids=archived_ids,
        )

        storage_reference_count += (
            chunk_references
        )

        safe_storage_object_count += (
            chunk_safe
        )

        ignored_storage_metadata_count += (
            chunk_ignored
        )

        unsafe_storage_reference_count += (
            chunk_unsafe
        )

    eligible_count = int(
        lifecycle_counts[
            "ARCHIVED"
        ]
    )

    active_count = int(
        lifecycle_counts[
            "ACTIVE"
        ]
    )

    graduated_count = int(
        lifecycle_counts[
            "GRADUATED"
        ]
    )

    already_purged_count = int(
        lifecycle_counts[
            "PURGED"
        ]
    )

    export_job = (
        await _latest_verified_export(
            db=db,
            scope=scope,
            batch_id=batch.id,
        )
    )

    verified_export_available = (
        export_job is not None
    )

    export_student_count_matches = (
        export_job is not None
        and int(
            export_job.students_rows
            or 0
        )
        == int(
            selected_count
        )
    )

    purge_ready = bool(
        eligible_count > 0
        and verified_export_available
        and export_student_count_matches
        and unsafe_storage_reference_count
        == 0
    )

    return WebsiteBatchPurgePreview(
        batch_id=int(
            batch.id
        ),

        selected_count=int(
            selected_count
        ),

        eligible_count=eligible_count,

        active_count=active_count,

        graduated_count=graduated_count,

        archived_count=eligible_count,

        already_purged_count=(
            already_purged_count
        ),

        skipped_count=max(
            0,
            int(
                selected_count
            )
            - eligible_count,
        ),

        verified_export_job_id=(
            int(
                export_job.id
            )
            if export_job is not None
            else None
        ),

        verified_export_available=(
            verified_export_available
        ),

        export_student_count_matches=(
            export_student_count_matches
        ),

        activity_sessions_count=int(
            candidate_counts[
                "activity_sessions"
            ]
        ),

        activity_photos_count=int(
            candidate_counts[
                "activity_photos"
            ]
        ),

        activity_face_checks_count=int(
            candidate_counts[
                "activity_face_checks"
            ]
        ),

        event_submission_photos_count=int(
            candidate_counts[
                "event_submission_photos"
            ]
        ),

        face_embeddings_count=int(
            candidate_counts[
                "student_face_embeddings"
            ]
        ),

        face_enrollment_images_count=int(
            candidate_counts[
                "student_face_enrollment_images"
            ]
        ),

        push_devices_count=int(
            candidate_counts[
                "student_push_devices"
            ]
        ),

        notification_deliveries_count=int(
            candidate_counts[
                "student_notification_deliveries"
            ]
        ),

        event_role_assignments_count=int(
            candidate_counts[
                "event_role_assignments"
            ]
        ),

        faculty_assignments_count=int(
            candidate_counts[
                "student_faculty_assignments"
            ]
        ),

        activity_progress_count=int(
            candidate_counts[
                "student_activity_progress"
            ]
        ),

        activity_stats_count=int(
            candidate_counts[
                "student_activity_stats"
            ]
        ),

        point_adjustments_count=int(
            candidate_counts[
                "student_point_adjustments"
            ]
        ),

        storage_reference_count=int(
            storage_reference_count
        ),

        safe_storage_object_count=int(
            safe_storage_object_count
        ),

        ignored_storage_metadata_count=int(
            ignored_storage_metadata_count
        ),

        unsafe_storage_reference_count=int(
            unsafe_storage_reference_count
        ),

        purge_ready=purge_ready,
    )



# ============================================================
# PURGE JOB CONTROL PLANE
# ============================================================


def _require_purge_management_role(
    scope: WebsiteFacultyScope,
) -> None:
    if scope.role not in (
        ROLE_COLLEGE_COORDINATOR,
        ROLE_HOD,
    ):
        raise ValueError(
            "Batch purge is available only to "
            "College Coordinator or HOD"
        )


def _purge_scope_department_id(
    scope: WebsiteFacultyScope,
) -> int | None:
    scope_type = scope.scope_type.value

    if scope_type == "department":
        if (
            scope.department_id is None
            or int(scope.department_id) < 1
        ):
            raise ValueError(
                "Department scope requires department_id"
            )

        return int(
            scope.department_id
        )

    if scope_type == "college":
        return None

    raise ValueError(
        "Unsupported purge scope"
    )


def _apply_purge_job_scope(
    statement,
    *,
    scope: WebsiteFacultyScope,
    batch_id: int,
):
    statement = statement.where(
        BatchPurgeJob.batch_id
        == int(batch_id),

        func.lower(
            func.trim(
                BatchPurgeJob.college
            )
        )
        == _normalized_college_key(
            scope.college
        ),

        BatchPurgeJob.scope_type
        == scope.scope_type.value,
    )

    if (
        scope.scope_type.value
        == "department"
    ):
        statement = statement.where(
            BatchPurgeJob.department_id
            == int(
                scope.department_id
            )
        )

    else:
        statement = statement.where(
            BatchPurgeJob.department_id.is_(
                None
            )
        )

    return statement


async def create_batch_purge_job(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    reason: str,
) -> BatchPurgeJob:
    """
    Create one durable PENDING purge job.

    IMPORTANT:
    This function does NOT:
    - delete PostgreSQL rows;
    - delete object-storage objects;
    - update Student.lifecycle_status;
    - enqueue a worker.

    It only creates durable control-plane metadata after the
    existing purge preview has proven the scope purge-ready.
    """

    _require_purge_management_role(
        scope
    )

    batch = await _resolve_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    department_id = (
        _purge_scope_department_id(
            scope
        )
    )

    preview = await preview_website_batch_purge(
        db=db,
        scope=scope,
        batch_id=int(
            batch.id
        ),
    )

    if preview.eligible_count < 1:
        raise ValueError(
            "No archived students are eligible for purge"
        )

    if not preview.verified_export_available:
        raise ValueError(
            "A completed verified final export "
            "is required before purge"
        )

    if not preview.export_student_count_matches:
        raise ValueError(
            "Student set changed after verified export; "
            "generate a new export"
        )

    if (
        preview.unsafe_storage_reference_count
        > 0
    ):
        raise ValueError(
            "Unsafe storage references must be resolved "
            "before purge"
        )

    if not preview.purge_ready:
        raise ValueError(
            "Batch scope is not ready for purge"
        )

    if (
        preview.verified_export_job_id
        is None
    ):
        raise ValueError(
            "Verified export evidence is missing"
        )

    export_job_id = int(
        preview.verified_export_job_id
    )

    # Confirm the exact export evidence still exists.
    export_job = (
        await db.execute(
            select(
                BatchExportJob
            )
            .where(
                BatchExportJob.id
                == export_job_id,

                BatchExportJob.batch_id
                == int(batch.id),

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
        )
    ).scalar_one_or_none()

    if export_job is None:
        raise ValueError(
            "Verified export evidence changed; "
            "refresh purge preview"
        )

    if department_id is None:
        if export_job.department_id is not None:
            raise ValueError(
                "Verified export scope mismatch"
            )

    elif (
        export_job.department_id is None
        or int(
            export_job.department_id
        )
        != department_id
    ):
        raise ValueError(
            "Verified export scope mismatch"
        )

    existing_statement = (
        select(
            BatchPurgeJob.id
        )
        .where(
            BatchPurgeJob.status.in_(
                (
                    "PENDING",
                    "PROCESSING",
                )
            )
        )
        .limit(1)
    )

    existing_statement = (
        _apply_purge_job_scope(
            existing_statement,
            scope=scope,
            batch_id=int(
                batch.id
            ),
        )
    )

    existing_id = (
        await db.execute(
            existing_statement
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise ValueError(
            "A purge job is already pending or "
            "processing for this batch scope"
        )

    normalized_reason = str(
        reason or ""
    ).strip()

    if len(normalized_reason) < 2:
        raise ValueError(
            "Purge reason is required"
        )

    if len(normalized_reason) > 500:
        raise ValueError(
            "Purge reason must not exceed 500 characters"
        )

    job = BatchPurgeJob(
        batch_id=int(
            batch.id
        ),
        college=str(
            scope.college
        ).strip(),
        department_id=department_id,
        scope_type=scope.scope_type.value,
        export_job_id=export_job_id,
        requested_by_faculty_id=(
            int(scope.faculty_id)
            if int(scope.faculty_id) > 0
            else None
        ),
        reason=normalized_reason,
        status="PENDING",

        # Snapshot of ARCHIVED Students currently eligible.
        students_targeted=int(
            preview.eligible_count
        ),
        students_purged=0,

        # The future worker will replace this with the exact
        # deduplicated physical-object count before deletion.
        storage_objects_targeted=0,
        storage_objects_deleted=0,

        ignored_storage_metadata=0,
        unsafe_storage_references=0,
    )

    db.add(job)

    try:
        await db.flush()

    except IntegrityError as exc:
        error_text = str(
            getattr(
                exc,
                "orig",
                exc,
            )
        )

        if (
            "uq_batch_purge_jobs_active_scope"
            in error_text
        ):
            raise ValueError(
                "A purge job is already pending or "
                "processing for this batch scope"
            ) from exc

        raise

    return job


async def get_batch_purge_job_within_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
    purge_job_id: int,
) -> BatchPurgeJob | None:
    """
    Return a purge job only inside the exact authenticated scope.
    """

    _require_purge_management_role(
        scope
    )

    if int(purge_job_id) < 1:
        return None

    batch = await _resolve_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    statement = select(
        BatchPurgeJob
    ).where(
        BatchPurgeJob.id
        == int(
            purge_job_id
        )
    )

    statement = _apply_purge_job_scope(
        statement,
        scope=scope,
        batch_id=int(
            batch.id
        ),
    )

    return (
        await db.execute(
            statement
        )
    ).scalar_one_or_none()


async def get_latest_batch_purge_job_within_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    batch_id: int,
) -> BatchPurgeJob | None:
    """
    Restore useful purge state after a website refresh.

    Priority:
    1. newest PENDING / PROCESSING job;
    2. otherwise newest job in exact scope.
    """

    _require_purge_management_role(
        scope
    )

    batch = await _resolve_batch(
        db=db,
        scope=scope,
        batch_id=batch_id,
    )

    active_statement = (
        select(
            BatchPurgeJob
        )
        .where(
            BatchPurgeJob.status.in_(
                (
                    "PENDING",
                    "PROCESSING",
                )
            )
        )
        .order_by(
            BatchPurgeJob.created_at.desc(),
            BatchPurgeJob.id.desc(),
        )
        .limit(1)
    )

    active_statement = _apply_purge_job_scope(
        active_statement,
        scope=scope,
        batch_id=int(
            batch.id
        ),
    )

    active_job = (
        await db.execute(
            active_statement
        )
    ).scalar_one_or_none()

    if active_job is not None:
        return active_job

    latest_statement = (
        select(
            BatchPurgeJob
        )
        .order_by(
            BatchPurgeJob.created_at.desc(),
            BatchPurgeJob.id.desc(),
        )
        .limit(1)
    )

    latest_statement = _apply_purge_job_scope(
        latest_statement,
        scope=scope,
        batch_id=int(
            batch.id
        ),
    )

    return (
        await db.execute(
            latest_statement
        )
    ).scalar_one_or_none()



# ============================================================
# DURABLE PURGE WORKER
# ============================================================


PURGE_WORKER_STUDENT_CHUNK_SIZE = 500


def _purge_actor_type(
    job: BatchPurgeJob,
) -> str:
    if str(job.scope_type) == "department":
        return "HOD"

    return "COLLEGE_COORDINATOR"


def _purge_actor_role(
    job: BatchPurgeJob,
) -> str:
    if str(job.scope_type) == "department":
        return ROLE_HOD

    return ROLE_COLLEGE_COORDINATOR


def _scope_from_purge_job(
    job: BatchPurgeJob,
) -> WebsiteFacultyScope:
    scope_type = str(
        job.scope_type
    )

    if scope_type == "department":
        if job.department_id is None:
            raise RuntimeError(
                "Department purge job is missing department_id"
            )

        return WebsiteFacultyScope(
            faculty_id=0,
            role=ROLE_HOD,
            scope_type=FacultyScopeType.DEPARTMENT,
            college=str(
                job.college
            ),
            department_id=int(
                job.department_id
            ),
        )

    if scope_type == "college":
        return WebsiteFacultyScope(
            faculty_id=0,
            role=ROLE_COLLEGE_COORDINATOR,
            scope_type=FacultyScopeType.COLLEGE,
            college=str(
                job.college
            ),
            department_id=None,
        )

    raise RuntimeError(
        "Unsupported purge-job scope"
    )


def _safe_purge_failure_reason(
    exc: Exception,
) -> str:
    value = str(
        exc or ""
    ).strip()

    if not value:
        value = exc.__class__.__name__

    return value[:4000]


def _apply_job_student_scope(
    statement,
    *,
    job: BatchPurgeJob,
):
    statement = statement.where(
        Student.batch_id
        == int(job.batch_id),

        func.lower(
            func.trim(
                Student.college
            )
        )
        == _normalized_college_key(
            job.college
        ),
    )

    if str(job.scope_type) == "department":
        if job.department_id is None:
            raise RuntimeError(
                "Department purge job is missing department_id"
            )

        statement = statement.where(
            Student.department_id
            == int(
                job.department_id
            )
        )

    elif str(job.scope_type) != "college":
        raise RuntimeError(
            "Unsupported purge-job scope"
        )

    return statement


async def _archived_student_chunk(
    *,
    db: AsyncSession,
    job: BatchPurgeJob,
    after_id: int,
    for_update: bool = False,
) -> list[int]:
    statement = (
        select(
            Student.id
        )
        .where(
            Student.lifecycle_status
            == "ARCHIVED",

            Student.id
            > int(after_id),
        )
        .order_by(
            Student.id.asc()
        )
        .limit(
            PURGE_WORKER_STUDENT_CHUNK_SIZE
        )
    )

    statement = _apply_job_student_scope(
        statement,
        job=job,
    )

    if for_update:
        statement = statement.with_for_update(
            of=Student
        )

    return [
        int(value)
        for value in (
            await db.execute(
                statement
            )
        ).scalars().all()
    ]


async def _collect_purge_storage_objects(
    *,
    db: AsyncSession,
    student_ids: list[int],
) -> tuple[
    list[PurgeStorageObject],
    int,
    int,
]:
    """
    Return:
        safe unique objects,
        ignored metadata count,
        unsafe reference count.

    No storage call occurs here.
    """

    if not student_ids:
        return (
            [],
            0,
            0,
        )

    objects: list[
        PurgeStorageObject
    ] = []

    ignored = 0
    unsafe = 0

    # --------------------------------------------------------
    # Activity photos
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _ACTIVITY_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        try:
            objects.append(
                activity_photo_object(
                    image_url=str(
                        row.image_url or ""
                    ),
                    student_id=int(
                        row.student_id
                    ),
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            unsafe += 1

    # --------------------------------------------------------
    # Event-submission photos
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _EVENT_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        try:
            objects.append(
                activity_photo_object(
                    image_url=str(
                        row.image_url or ""
                    ),
                    student_id=int(
                        row.student_id
                    ),
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            unsafe += 1

    # --------------------------------------------------------
    # Face enrollment
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _ENROLLMENT_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        stored_value = (
            row.image_key
            or row.image_url
            or ""
        )

        try:
            objects.append(
                face_enrollment_object(
                    image_key=str(
                        stored_value
                    ),
                    student_id=int(
                        row.student_id
                    ),
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            unsafe += 1

    # --------------------------------------------------------
    # Processed / boxed faces
    # --------------------------------------------------------

    rows = (
        await db.execute(
            _PROCESSED_FACE_STORAGE_QUERY,
            {
                "student_ids": student_ids,
            },
        )
    ).all()

    for row in rows:
        try:
            item = processed_face_object(
                processed_object=(
                    row.processed_object
                ),
                session_id=int(
                    row.session_id
                ),
                photo_id=int(
                    row.photo_id
                ),
            )

            if item is None:
                ignored += 1
            else:
                objects.append(
                    item
                )

        except (
            TypeError,
            ValueError,
        ):
            unsafe += 1

    return (
        list(
            deduplicate_purge_objects(
                objects
            )
        ),
        int(
            ignored
        ),
        int(
            unsafe
        ),
    )


def _delete_statement(
    sql: str,
):
    return text(
        sql
    ).bindparams(
        bindparam(
            "student_ids",
            expanding=True,
        )
    )


_PURGE_DELETE_STATEMENTS = (
    (
        "activity_face_checks_deleted",
        _delete_statement(
            """
            DELETE FROM activity_face_checks
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "activity_photos_deleted",
        _delete_statement(
            """
            DELETE FROM activity_photos
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "activity_sessions_deleted",
        _delete_statement(
            """
            DELETE FROM activity_sessions
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "event_submission_photos_deleted",
        _delete_statement(
            """
            DELETE FROM event_submission_photos esp
            USING event_submissions es
            WHERE esp.submission_id = es.id
              AND es.student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "face_embeddings_deleted",
        _delete_statement(
            """
            DELETE FROM student_face_embeddings
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "face_enrollment_images_deleted",
        _delete_statement(
            """
            DELETE FROM student_face_enrollment_images
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "push_devices_deleted",
        _delete_statement(
            """
            DELETE FROM student_push_devices
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "notification_deliveries_deleted",
        _delete_statement(
            """
            DELETE FROM student_notification_deliveries
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "event_role_assignments_deleted",
        _delete_statement(
            """
            DELETE FROM event_role_assignments
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "faculty_assignments_deleted",
        _delete_statement(
            """
            DELETE FROM student_faculty_assignments
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "activity_progress_deleted",
        _delete_statement(
            """
            DELETE FROM student_activity_progress
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "activity_stats_deleted",
        _delete_statement(
            """
            DELETE FROM student_activity_stats
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
    (
        "point_adjustments_deleted",
        _delete_statement(
            """
            DELETE FROM student_point_adjustments
            WHERE student_id IN :student_ids
            RETURNING 1
            """
        ),
    ),
)


_PURGE_STUDENT_UPDATE = (
    text(
        """
        UPDATE students
        SET
            lifecycle_status = 'PURGED',
            purged_at = CURRENT_TIMESTAMP
        WHERE id IN :student_ids
          AND lifecycle_status = 'ARCHIVED'
        RETURNING id
        """
    )
    .bindparams(
        bindparam(
            "student_ids",
            expanding=True,
        )
    )
)


async def _verify_purge_job_preconditions(
    *,
    db: AsyncSession,
    job: BatchPurgeJob,
) -> None:
    """
    Re-check all destructive gates immediately before processing.
    """

    scope = _scope_from_purge_job(
        job
    )

    preview = await preview_website_batch_purge(
        db=db,
        scope=scope,
        batch_id=int(
            job.batch_id
        ),
    )

    if not preview.purge_ready:
        raise RuntimeError(
            "Purge preview is no longer ready"
        )

    if (
        preview.verified_export_job_id
        is None
        or int(
            preview.verified_export_job_id
        )
        != int(
            job.export_job_id
        )
    ):
        raise RuntimeError(
            "Verified export evidence changed after purge job creation"
        )

    if (
        not preview.export_student_count_matches
    ):
        raise RuntimeError(
            "Student set changed after verified export"
        )

    if (
        int(
            preview.unsafe_storage_reference_count
        )
        != 0
    ):
        raise RuntimeError(
            "Unsafe storage references detected"
        )

    if (
        int(
            preview.eligible_count
        )
        != int(
            job.students_targeted
        )
    ):
        raise RuntimeError(
            "Archived student set changed after purge job creation"
        )

    export_job = (
        await db.execute(
            select(
                BatchExportJob
            )
            .where(
                BatchExportJob.id
                == int(
                    job.export_job_id
                ),

                BatchExportJob.batch_id
                == int(
                    job.batch_id
                ),

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
        )
    ).scalar_one_or_none()

    if export_job is None:
        raise RuntimeError(
            "Verified export evidence is no longer valid"
        )

    if (
        _normalized_college_key(
            export_job.college
        )
        != _normalized_college_key(
            job.college
        )
        or str(
            export_job.scope_type
        )
        != str(
            job.scope_type
        )
    ):
        raise RuntimeError(
            "Verified export scope no longer matches purge job"
        )

    if str(job.scope_type) == "department":
        if (
            export_job.department_id is None
            or job.department_id is None
            or int(
                export_job.department_id
            )
            != int(
                job.department_id
            )
        ):
            raise RuntimeError(
                "Verified export department no longer matches purge job"
            )

    elif export_job.department_id is not None:
        raise RuntimeError(
            "College-scope export unexpectedly has department_id"
        )


async def process_batch_purge_job(
    *,
    db: AsyncSession,
    purge_job_id: int,
) -> BatchPurgeJob:
    """
    Execute one durable batch purge.

    Cross-system safety model
    -------------------------
    PostgreSQL and S3/MinIO cannot share one atomic transaction.

    Therefore this worker intentionally uses two phases:

    PHASE A
        validate every current reference and delete only strictly
        validated storage objects.

    PHASE B
        delete approved operational PostgreSQL rows in bounded
        chunks and transition those Student rows:
            ARCHIVED -> PURGED

    Storage deletion is idempotent. If storage succeeds but a later
    DB transaction fails, a future purge job can safely request
    deletion of the same now-missing objects and finish DB cleanup.

    Protected records are never deleted here:
    - students identity rows
    - certificates
    - event_submissions
    - event_participants
    - external participant link history
    - student_academic_history
    - batch_export_jobs
    - audit_logs
    """

    purge_job_id = int(
        purge_job_id
    )

    if purge_job_id < 1:
        raise ValueError(
            "purge_job_id must be greater than zero"
        )

    # ========================================================
    # CLAIM JOB
    # ========================================================

    try:
        job = (
            await db.execute(
                select(
                    BatchPurgeJob
                )
                .where(
                    BatchPurgeJob.id
                    == purge_job_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if job is None:
            raise ValueError(
                "Batch purge job not found"
            )

        if str(job.status) == "COMPLETED":
            return job

        if str(job.status) != "PENDING":
            raise ValueError(
                "Batch purge job is not pending"
            )

        await _verify_purge_job_preconditions(
            db=db,
            job=job,
        )

        job.status = "PROCESSING"
        job.started_at = func.now()
        job.failure_reason = None

        # Worker accounting starts from a clean durable baseline.
        job.students_purged = 0

        job.activity_sessions_deleted = 0
        job.activity_photos_deleted = 0
        job.activity_face_checks_deleted = 0
        job.event_submission_photos_deleted = 0
        job.face_embeddings_deleted = 0
        job.face_enrollment_images_deleted = 0
        job.push_devices_deleted = 0
        job.notification_deliveries_deleted = 0
        job.event_role_assignments_deleted = 0
        job.faculty_assignments_deleted = 0
        job.activity_progress_deleted = 0
        job.activity_stats_deleted = 0
        job.point_adjustments_deleted = 0

        job.storage_objects_targeted = 0
        job.storage_objects_deleted = 0
        job.ignored_storage_metadata = 0
        job.unsafe_storage_references = 0

        job.storage_completed_at = None
        job.database_completed_at = None
        job.completed_at = None

        await db.commit()

        # ====================================================
        # PHASE A — OBJECT STORAGE
        # ====================================================

        cursor = 0

        while True:
            job = await db.get(
                BatchPurgeJob,
                purge_job_id,
            )

            if job is None:
                raise RuntimeError(
                    "Batch purge job disappeared"
                )

            student_ids = await _archived_student_chunk(
                db=db,
                job=job,
                after_id=cursor,
                for_update=False,
            )

            if not student_ids:
                break

            (
                objects,
                ignored_count,
                unsafe_count,
            ) = await _collect_purge_storage_objects(
                db=db,
                student_ids=student_ids,
            )

            if unsafe_count:
                job.unsafe_storage_references = (
                    int(
                        job.unsafe_storage_references
                        or 0
                    )
                    + int(
                        unsafe_count
                    )
                )

                await db.commit()

                raise RuntimeError(
                    "Unsafe storage references detected "
                    "during purge processing"
                )

            object_count = len(
                objects
            )

            if object_count:
                # This is the only physical object-storage deletion
                # point in the purge worker.
                delete_purge_storage_objects(
                    objects
                )

            job = (
                await db.execute(
                    select(
                        BatchPurgeJob
                    )
                    .where(
                        BatchPurgeJob.id
                        == purge_job_id
                    )
                    .with_for_update()
                )
            ).scalar_one()

            job.storage_objects_targeted = (
                int(
                    job.storage_objects_targeted
                    or 0
                )
                + object_count
            )

            # A successful delete call means the object deletion
            # request was accepted. Missing objects are intentionally
            # treated idempotently as already deleted.
            job.storage_objects_deleted = (
                int(
                    job.storage_objects_deleted
                    or 0
                )
                + object_count
            )

            job.ignored_storage_metadata = (
                int(
                    job.ignored_storage_metadata
                    or 0
                )
                + int(
                    ignored_count
                )
            )

            await db.commit()

            cursor = int(
                student_ids[-1]
            )

        job = (
            await db.execute(
                select(
                    BatchPurgeJob
                )
                .where(
                    BatchPurgeJob.id
                    == purge_job_id
                )
                .with_for_update()
            )
        ).scalar_one()

        job.storage_completed_at = func.now()

        await db.commit()

        # ====================================================
        # PHASE B — POSTGRESQL + ARCHIVED -> PURGED
        # ====================================================

        cursor = 0

        while True:
            job = await db.get(
                BatchPurgeJob,
                purge_job_id,
            )

            if job is None:
                raise RuntimeError(
                    "Batch purge job disappeared"
                )

            student_ids = await _archived_student_chunk(
                db=db,
                job=job,
                after_id=cursor,
                for_update=True,
            )

            if not student_ids:
                break

            counts: dict[
                str,
                int,
            ] = {}

            for (
                field_name,
                statement,
            ) in _PURGE_DELETE_STATEMENTS:
                result = await db.execute(
                    statement,
                    {
                        "student_ids": student_ids,
                    },
                )

                counts[
                    field_name
                ] = len(
                    result.all()
                )

            transitioned = (
                await db.execute(
                    _PURGE_STUDENT_UPDATE,
                    {
                        "student_ids": student_ids,
                    },
                )
            ).scalars().all()

            if len(
                transitioned
            ) != len(
                student_ids
            ):
                raise RuntimeError(
                    "Archived student set changed during purge processing"
                )

            job = (
                await db.execute(
                    select(
                        BatchPurgeJob
                    )
                    .where(
                        BatchPurgeJob.id
                        == purge_job_id
                    )
                    .with_for_update()
                )
            ).scalar_one()

            for (
                field_name,
                deleted_count,
            ) in counts.items():
                setattr(
                    job,
                    field_name,
                    int(
                        getattr(
                            job,
                            field_name,
                        )
                        or 0
                    )
                    + int(
                        deleted_count
                    ),
                )

            job.students_purged = (
                int(
                    job.students_purged
                    or 0
                )
                + len(
                    transitioned
                )
            )

            await append_audit_log(
                db,
                actor_type=_purge_actor_type(
                    job
                ),
                actor_id=(
                    int(
                        job.requested_by_faculty_id
                    )
                    if (
                        job.requested_by_faculty_id
                        is not None
                    )
                    else None
                ),
                actor_role=_purge_actor_role(
                    job
                ),
                college=str(
                    job.college
                ),
                department_id=(
                    int(
                        job.department_id
                    )
                    if (
                        job.department_id
                        is not None
                    )
                    else None
                ),
                action="STUDENTS_PURGED",
                description=(
                    "Purged archived student operational "
                    "data after verified final export"
                ),
                entity_type="batch_purge_job",
                entity_id=int(
                    job.id
                ),
                source="batch_purge_worker",
                metadata={
                    "purge_job_id": int(
                        job.id
                    ),
                    "export_job_id": int(
                        job.export_job_id
                    ),
                    "chunk_students": len(
                        transitioned
                    ),
                    "students_purged_total": int(
                        job.students_purged
                    ),
                    "reason": str(
                        job.reason
                    ),
                    "deleted_rows": {
                        key: int(
                            value
                        )
                        for (
                            key,
                            value,
                        ) in counts.items()
                    },
                },
                commit=False,
            )

            await db.commit()

            cursor = int(
                student_ids[-1]
            )

        # ====================================================
        # FINAL COMPLETION CHECK
        # ====================================================

        job = (
            await db.execute(
                select(
                    BatchPurgeJob
                )
                .where(
                    BatchPurgeJob.id
                    == purge_job_id
                )
                .with_for_update()
            )
        ).scalar_one()

        if (
            int(
                job.students_purged
                or 0
            )
            != int(
                job.students_targeted
                or 0
            )
        ):
            raise RuntimeError(
                "Purge completed student count does not match target"
            )

        if (
            int(
                job.unsafe_storage_references
                or 0
            )
            != 0
        ):
            raise RuntimeError(
                "Purge cannot complete with unsafe storage references"
            )

        job.database_completed_at = func.now()
        job.completed_at = func.now()
        job.status = "COMPLETED"
        job.failure_reason = None

        await append_audit_log(
            db,
            actor_type=_purge_actor_type(
                job
            ),
            actor_id=(
                int(
                    job.requested_by_faculty_id
                )
                if (
                    job.requested_by_faculty_id
                    is not None
                )
                else None
            ),
            actor_role=_purge_actor_role(
                job
            ),
            college=str(
                job.college
            ),
            department_id=(
                int(
                    job.department_id
                )
                if (
                    job.department_id
                    is not None
                )
                else None
            ),
            action="BATCH_PURGE_COMPLETED",
            description=(
                "Completed archived batch purge "
                "after verified final export"
            ),
            entity_type="batch_purge_job",
            entity_id=int(
                job.id
            ),
            source="batch_purge_worker",
            metadata={
                "purge_job_id": int(
                    job.id
                ),
                "export_job_id": int(
                    job.export_job_id
                ),
                "students_targeted": int(
                    job.students_targeted
                ),
                "students_purged": int(
                    job.students_purged
                ),
                "storage_objects_targeted": int(
                    job.storage_objects_targeted
                ),
                "storage_objects_deleted": int(
                    job.storage_objects_deleted
                ),
                "reason": str(
                    job.reason
                ),
            },
            commit=False,
        )

        await db.commit()

        refreshed = await db.get(
            BatchPurgeJob,
            purge_job_id,
        )

        if refreshed is None:
            raise RuntimeError(
                "Completed purge job could not be reloaded"
            )

        # func.now() checkpoint assignments are server-side SQL
        # expressions. Refresh explicitly so callers never receive
        # expired ORM attributes that could trigger MissingGreenlet.
        await db.refresh(refreshed)

        return refreshed

    except Exception as exc:
        await db.rollback()

        # Persist durable failure state if the job exists.
        failed_job = (
            await db.execute(
                select(
                    BatchPurgeJob
                )
                .where(
                    BatchPurgeJob.id
                    == purge_job_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if (
            failed_job is not None
            and str(
                failed_job.status
            )
            != "COMPLETED"
        ):
            failed_job.status = "FAILED"
            failed_job.completed_at = None
            failed_job.failure_reason = (
                _safe_purge_failure_reason(
                    exc
                )
            )

            await db.commit()

        raise
