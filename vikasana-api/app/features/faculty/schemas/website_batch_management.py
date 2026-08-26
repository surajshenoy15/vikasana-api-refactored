from datetime import datetime
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)


# =========================================================
# BATCH MANAGEMENT - READ MODELS
# =========================================================


class WebsiteBatchYearCount(BaseModel):
    year: int = Field(ge=1, le=8)
    student_count: int = Field(ge=0)


class WebsiteBatchSummary(BaseModel):
    """
    One academic batch as visible inside the authenticated
    Website Faculty scope.

    AcademicBatch itself remains college-wide.

    For an HOD, all counts will later be restricted to:
        authenticated college + authenticated department.

    For a College Coordinator, counts will later be restricted
    to the authenticated college and may optionally be filtered
    by a permitted department.
    """

    batch_id: int = Field(ge=1)
    name: str

    admitted_year: int
    passout_year: int
    course_duration_years: int = Field(ge=1, le=8)

    is_active: bool

    total_students: int = Field(ge=0)
    active_students: int = Field(ge=0)
    inactive_students: int = Field(ge=0)

    year_counts: list[WebsiteBatchYearCount]


class WebsiteBatchStudent(BaseModel):
    """
    Lightweight student row for Batch Management.

    This intentionally avoids returning activity/certificate
    aggregates for every student row.
    """

    id: int = Field(ge=1)

    name: str
    usn: str
    email: str | None = None

    department_id: int | None = None
    department_name: str | None = None

    batch_id: int = Field(ge=1)
    batch_name: str

    current_year: int | None = Field(
        default=None,
        ge=1,
        le=8,
    )

    assigned_faculty_id: int | None = None
    assigned_faculty_name: str | None = None

    is_active: bool


# =========================================================
# MANUAL MOVE YEAR
# =========================================================


class WebsiteBatchStudentPage(BaseModel):
    """
    Bounded cursor-paginated student result.

    The API never returns an unbounded batch/student collection.
    next_cursor is the last returned Student.id and can be supplied
    to fetch the next page.
    """

    items: list[WebsiteBatchStudent] = Field(
        default_factory=list,
    )
    returned_count: int = Field(
        ge=0,
    )
    limit: int = Field(
        ge=1,
        le=200,
    )
    next_cursor: int | None = Field(
        default=None,
        ge=1,
    )
    has_more: bool


class WebsiteStudentMoveYearRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    """
    Manually move one scoped student to another academic year.

    Exactly one lookup value must be supplied:
        usn
        email

    Supports:
        promotion
        demotion
        academic correction

    department_id, batch_id and college are intentionally not
    accepted from the client.
    """

    usn: str | None = Field(
        default=None,
        min_length=2,
        max_length=30,
    )
    email: EmailStr | None = None

    to_year: int = Field(
        ge=1,
        le=8,
    )

    reason: str = Field(
        min_length=2,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_student_lookup(
        self,
    ) -> "WebsiteStudentMoveYearRequest":
        normalized_usn = str(
            self.usn or ""
        ).strip()

        normalized_email = str(
            self.email or ""
        ).strip()

        supplied = sum(
            bool(value)
            for value in (
                normalized_usn,
                normalized_email,
            )
        )

        if supplied != 1:
            raise ValueError(
                "Provide exactly one of usn or email"
            )

        if normalized_usn:
            self.usn = normalized_usn

        return self


class WebsiteStudentMoveYearResponse(BaseModel):
    """
    Result of one manual academic-year move.

    batch_id and department_id are returned for verification,
    but are never supplied or changed by the client.
    """

    student_id: int = Field(
        ge=1,
    )
    name: str
    usn: str
    email: str | None = None

    department_id: int | None = Field(
        default=None,
        ge=1,
    )
    batch_id: int = Field(
        ge=1,
    )

    from_year: int = Field(
        ge=1,
        le=8,
    )
    to_year: int = Field(
        ge=1,
        le=8,
    )

    reason: str

    history_created: bool = True


class WebsiteBatchPromoteSelectedRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    """
    Promote selected students inside one batch.

    from_year is required so the backend can reject stale
    or mismatched selections safely.
    """

    student_ids: list[int] = Field(
        min_length=1,
        max_length=1000,
    )

    from_year: int = Field(
        ge=1,
        le=8,
    )

    to_year: int = Field(
        ge=1,
        le=8,
    )

    reason: str = Field(
        default="Academic year promotion",
        min_length=2,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_promotion(
        self,
    ) -> "WebsiteBatchPromoteSelectedRequest":
        if len(self.student_ids) != len(
            set(self.student_ids)
        ):
            raise ValueError(
                "student_ids must not contain duplicates"
            )

        if self.to_year != self.from_year + 1:
            raise ValueError(
                "Batch promotion must move students "
                "exactly one academic year forward"
            )

        return self


# =========================================================
# ENTIRE YEAR PROMOTION
# =========================================================


class WebsiteBatchPromoteYearRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    """
    Promote all eligible active students from one year to
    the immediately following year.

    Student IDs are deliberately NOT sent by the frontend.
    The backend will select eligible students from the
    authenticated scope.
    """

    from_year: int = Field(
        ge=1,
        le=8,
    )

    to_year: int = Field(
        ge=1,
        le=8,
    )

    reason: str = Field(
        default="Academic year promotion",
        min_length=2,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_promotion(
        self,
    ) -> "WebsiteBatchPromoteYearRequest":
        if self.to_year != self.from_year + 1:
            raise ValueError(
                "Batch promotion must move students "
                "exactly one academic year forward"
            )

        return self


# =========================================================
# PROMOTION RESULT
# =========================================================


class WebsiteBatchPromotionResponse(BaseModel):
    batch_id: int = Field(ge=1)

    from_year: int = Field(
        ge=1,
        le=8,
    )

    to_year: int = Field(
        ge=1,
        le=8,
    )

    selected_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    promoted_count: int = Field(ge=0)

    inactive_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)

    promoted_student_ids: list[int] = Field(
        default_factory=list,
    )


class WebsiteBatchPromotionPreview(BaseModel):
    batch_id: int = Field(
        ...,
        ge=1,
    )

    from_year: int = Field(
        ...,
        ge=1,
        le=8,
    )

    to_year: int = Field(
        ...,
        ge=1,
        le=8,
    )

    selected_count: int = Field(
        ...,
        ge=0,
    )

    eligible_count: int = Field(
        ...,
        ge=0,
    )

    inactive_count: int = Field(
        ...,
        ge=0,
    )


class WebsiteBatchGraduationPreview(BaseModel):
    """
    Read-only final-year graduation eligibility summary.

    selected_count:
        Every scoped Student in the batch's final academic year.

    eligible_count:
        lifecycle_status == ACTIVE and is_active == True.

    inactive_count:
        lifecycle_status == ACTIVE but legacy is_active == False.

    already_graduated_count / archived_count / purged_count:
        Students already beyond the ACTIVE lifecycle.

    skipped_count:
        selected_count - eligible_count.
    """

    batch_id: int = Field(
        ...,
        ge=1,
    )

    final_year: int = Field(
        ...,
        ge=1,
        le=8,
    )

    selected_count: int = Field(
        ...,
        ge=0,
    )

    eligible_count: int = Field(
        ...,
        ge=0,
    )

    inactive_count: int = Field(
        ...,
        ge=0,
    )

    already_graduated_count: int = Field(
        ...,
        ge=0,
    )

    archived_count: int = Field(
        ...,
        ge=0,
    )

    purged_count: int = Field(
        ...,
        ge=0,
    )

    skipped_count: int = Field(
        ...,
        ge=0,
    )


class WebsiteBatchGraduateRequest(BaseModel):
    """
    Confirm a scoped final-year graduation operation.

    Scope is always derived from the authenticated website Faculty.
    The client cannot provide college, department or student IDs.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    reason: str = Field(
        default="Final year graduation",
        min_length=2,
        max_length=500,
    )


class WebsiteBatchGraduationResponse(BaseModel):
    batch_id: int = Field(
        ...,
        ge=1,
    )

    final_year: int = Field(
        ...,
        ge=1,
        le=8,
    )

    selected_count: int = Field(
        ...,
        ge=0,
    )

    eligible_count: int = Field(
        ...,
        ge=0,
    )

    graduated_count: int = Field(
        ...,
        ge=0,
    )

    inactive_count: int = Field(
        ...,
        ge=0,
    )

    already_graduated_count: int = Field(
        ...,
        ge=0,
    )

    archived_count: int = Field(
        ...,
        ge=0,
    )

    purged_count: int = Field(
        ...,
        ge=0,
    )

    skipped_count: int = Field(
        ...,
        ge=0,
    )

    audit_log_id: int | None = Field(
        default=None,
        ge=1,
    )


class WebsiteBatchArchivePreview(BaseModel):
    """
    Read-only lifecycle distribution before archiving a batch scope.

    eligible_count:
        Students currently in lifecycle GRADUATED.

    legacy_inactive_eligible_count:
        Eligible GRADUATED Students whose legacy account-level
        is_active flag is False. They remain archive-eligible because
        lifecycle state and legacy account activation are independent.
    """

    batch_id: int = Field(
        ...,
        ge=1,
    )

    selected_count: int = Field(
        ...,
        ge=0,
    )

    eligible_count: int = Field(
        ...,
        ge=0,
    )

    active_count: int = Field(
        ...,
        ge=0,
    )

    graduated_count: int = Field(
        ...,
        ge=0,
    )

    archived_count: int = Field(
        ...,
        ge=0,
    )

    purged_count: int = Field(
        ...,
        ge=0,
    )

    legacy_inactive_eligible_count: int = Field(
        ...,
        ge=0,
    )

    skipped_count: int = Field(
        ...,
        ge=0,
    )




class WebsiteBatchExportJobResponse(BaseModel):
    id: int
    batch_id: int
    scope_type: str
    department_id: int | None = None

    status: str

    students_rows: int = 0
    academic_history_rows: int = 0
    activity_records_rows: int = 0
    activity_points_rows: int = 0
    event_participation_rows: int = 0
    certificates_rows: int = 0

    file_size_bytes: int | None = None
    sha256: str | None = None

    started_at: datetime | None = None
    completed_at: datetime | None = None
    verified_at: datetime | None = None

    failure_reason: str | None = None
    created_at: datetime


class WebsiteBatchExportDownloadResponse(BaseModel):
    export_job_id: int
    filename: str
    url: str
    expires_in_seconds: int
    sha256: str
    file_size_bytes: int



class WebsiteBatchArchiveRequest(BaseModel):
    """
    Archive GRADUATED Students only after a verified final export.

    Scope, batch and Student IDs are server-derived.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    reason: str = Field(
        default="Final batch archive after verified export",
        min_length=2,
        max_length=500,
    )


class WebsiteBatchArchiveResponse(BaseModel):
    batch_id: int = Field(..., ge=1)
    export_job_id: int = Field(..., ge=1)

    selected_count: int = Field(..., ge=0)
    eligible_count: int = Field(..., ge=0)

    archived_count: int = Field(..., ge=0)
    active_count: int = Field(..., ge=0)
    already_archived_count: int = Field(..., ge=0)
    purged_count: int = Field(..., ge=0)

    legacy_inactive_eligible_count: int = Field(..., ge=0)
    skipped_count: int = Field(..., ge=0)

    audit_log_id: int | None = Field(
        default=None,
        ge=1,
    )


# ============================================================
# BATCH PURGE PREVIEW
# ============================================================


class WebsiteBatchPurgePreview(BaseModel):
    batch_id: int

    # Student lifecycle population inside exact authenticated scope.
    selected_count: int
    eligible_count: int
    active_count: int
    graduated_count: int
    archived_count: int
    already_purged_count: int
    skipped_count: int

    # Exact verified-export evidence.
    verified_export_job_id: int | None = None
    verified_export_available: bool
    export_student_count_matches: bool

    # PostgreSQL rows which would be removed for ARCHIVED students.
    activity_sessions_count: int
    activity_photos_count: int
    activity_face_checks_count: int

    event_submission_photos_count: int

    face_embeddings_count: int
    face_enrollment_images_count: int

    push_devices_count: int
    notification_deliveries_count: int

    event_role_assignments_count: int
    faculty_assignments_count: int

    activity_progress_count: int
    activity_stats_count: int
    point_adjustments_count: int

    # Object-storage validation.
    storage_reference_count: int
    safe_storage_object_count: int
    ignored_storage_metadata_count: int
    unsafe_storage_reference_count: int

    # True only when the eventual purge mutation may be offered.
    purge_ready: bool



# ============================================================
# BATCH PURGE JOB CONTROL PLANE
# ============================================================


class WebsiteBatchPurgeRequest(BaseModel):
    """
    Request creation of a durable ARCHIVED -> PURGED job.

    Student IDs, scope and verified export evidence are all
    resolved server-side.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    reason: str = Field(
        default=(
            "Purge archived batch operational data "
            "after verified final export"
        ),
        min_length=2,
        max_length=500,
    )


class WebsiteBatchPurgeJobResponse(BaseModel):
    id: int = Field(..., ge=1)
    batch_id: int = Field(..., ge=1)
    export_job_id: int = Field(..., ge=1)

    scope_type: str
    department_id: int | None = None

    reason: str
    status: str

    students_targeted: int = Field(..., ge=0)
    students_purged: int = Field(..., ge=0)

    activity_sessions_deleted: int = Field(..., ge=0)
    activity_photos_deleted: int = Field(..., ge=0)
    activity_face_checks_deleted: int = Field(..., ge=0)
    event_submission_photos_deleted: int = Field(..., ge=0)

    face_embeddings_deleted: int = Field(..., ge=0)
    face_enrollment_images_deleted: int = Field(..., ge=0)

    push_devices_deleted: int = Field(..., ge=0)
    notification_deliveries_deleted: int = Field(..., ge=0)

    event_role_assignments_deleted: int = Field(..., ge=0)
    faculty_assignments_deleted: int = Field(..., ge=0)

    activity_progress_deleted: int = Field(..., ge=0)
    activity_stats_deleted: int = Field(..., ge=0)
    point_adjustments_deleted: int = Field(..., ge=0)

    storage_objects_targeted: int = Field(..., ge=0)
    storage_objects_deleted: int = Field(..., ge=0)
    ignored_storage_metadata: int = Field(..., ge=0)
    unsafe_storage_references: int = Field(..., ge=0)

    started_at: datetime | None = None
    storage_completed_at: datetime | None = None
    database_completed_at: datetime | None = None
    completed_at: datetime | None = None

    failure_reason: str | None = None
    created_at: datetime
