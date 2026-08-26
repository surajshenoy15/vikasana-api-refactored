"""
Final academic-batch export format contract.

The final ZIP contains exactly six CSV files:

1. students.csv
2. academic_history.csv
3. activity_records.csv
4. activity_points.csv
5. event_participation.csv
6. certificates.csv

Important rules:

- Export rows are historical snapshots.
- Points are NEVER recomputed during export.
- activity_points.csv contains both transaction and aggregate
  snapshot rows, distinguished by record_type.
- EVENT_CREDIT rows represent the total EventSubmission credit.
  They must not invent per-activity-type point allocation.
- ACTIVITY_TYPE_SNAPSHOT rows preserve StudentActivityStats.
- Archive/purge logic is not implemented here.
"""


BATCH_EXPORT_FORMAT_VERSION = 1


# =========================================================
# COMMON CONTEXT
# =========================================================

EXPORT_CONTEXT_COLUMNS = (
    "export_job_id",
    "export_batch_id",
    "export_scope_type",
    "export_department_id",
)


# =========================================================
# 1. students.csv
# =========================================================

STUDENTS_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "student_id",
    "college",
    "name",
    "usn",
    "branch",
    "email",
    "student_type",

    "is_active",
    "lifecycle_status",
    "graduated_at",
    "archived_at",
    "purged_at",

    "required_total_points",
    "total_points_earned",

    "face_enrolled",
    "face_enrolled_at",

    "passout_year",
    "admitted_year",

    "department_id",
    "department_name",
    "department_code",

    "batch_id",
    "batch_name",

    "current_year",

    "assigned_faculty_id",
    "assigned_faculty_name",
    "assigned_faculty_email",

    "created_at",
    "created_by_faculty_id",
)


# =========================================================
# 2. academic_history.csv
# =========================================================

ACADEMIC_HISTORY_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "history_id",

    "student_id",
    "student_usn",
    "student_name",

    "action",

    "from_department_id",
    "from_department_name",

    "to_department_id",
    "to_department_name",

    "from_batch_id",
    "from_batch_name",

    "to_batch_id",
    "to_batch_name",

    "from_year",
    "to_year",

    "academic_session",
    "reason",

    "changed_by_faculty_id",
    "changed_by_admin_id",

    "created_at",
)


# =========================================================
# 3. activity_records.csv
# =========================================================
#
# One CSV intentionally stores multiple evidence-record types so
# the final archive remains exactly six CSV files.
#
# SESSION:
#     one ActivitySession row.
#
# PHOTO:
#     one ActivityPhoto row.
#
# FACE_CHECK:
#     one ActivityFaceCheck row.
#
# record_id always refers to the source row represented by that
# record_type.
# =========================================================

ACTIVITY_RECORD_TYPES = (
    "SESSION",
    "PHOTO",
    "FACE_CHECK",
)


ACTIVITY_RECORDS_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "record_type",
    "record_id",

    "student_id",
    "student_usn",
    "student_name",

    "activity_session_id",

    "activity_type_id",
    "activity_type_name",

    "event_id",
    "event_title",
    "event_submission_id",

    # Session fields.
    "session_status",
    "activity_name",
    "description",
    "session_code",

    "started_at",
    "expires_at",
    "submitted_at",

    "duration_hours",
    "flag_reason",
    "points_awarded_at",

    # Photo fields.
    "photo_id",
    "photo_seq_no",
    "image_reference",

    "photo_lat",
    "photo_lng",
    "photo_captured_at",
    "photo_sha256",

    "distance_m",
    "is_in_geofence",
    "geo_flag_reason",

    # Face-check fields.
    "face_check_id",
    "face_matched",
    "cosine_score",
    "l2_score",
    "total_faces",
    "processed_object",
    "face_reason",

    "created_at",
    "updated_at",
)


# =========================================================
# 4. activity_points.csv
# =========================================================
#
# EVENT_CREDIT
# ------------
# Source:
#     EventSubmission where points_credited = TRUE.
#
# delta_points:
#     EventSubmission.awarded_points.
#
# IMPORTANT:
# An Event may map to multiple ActivityTypes. The persisted
# EventSubmission stores only the total points awarded for the
# submission, not a durable per-ActivityType transaction split.
#
# Therefore:
# - activity_type_id/name remain blank for EVENT_CREDIT.
# - event_activity_type_ids/names show the Event mappings.
# - export MUST NOT invent a per-type allocation.
#
#
# POINT_ADJUSTMENT
# ----------------
# Source:
#     StudentPointAdjustment.
#
# Covers:
# - manual/admin point entries
# - AUTO_AWARD_SESSION_<session_id> entries
#
#
# ACTIVITY_TYPE_SNAPSHOT
# ----------------------
# Source:
#     StudentActivityStats.
#
# This is an aggregate final snapshot and is NOT a transaction.
# Consumers must never sum snapshot points together with transaction
# delta_points.
# =========================================================

ACTIVITY_POINT_RECORD_TYPES = (
    "EVENT_CREDIT",
    "POINT_ADJUSTMENT",
    "ACTIVITY_TYPE_SNAPSHOT",
)


ACTIVITY_POINTS_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "record_type",
    "record_id",

    "student_id",
    "student_usn",
    "student_name",

    "activity_type_id",
    "activity_type_name",

    # For EVENT_CREDIT we preserve mappings without pretending
    # the total points were allocated equally/per-type.
    "event_activity_type_ids",
    "event_activity_type_names",

    "event_id",
    "event_title",
    "event_submission_id",

    "activity_session_id",

    # Transaction amount.
    "delta_points",

    # Aggregate per-ActivityType snapshot.
    "activity_type_total_points",
    "total_verified_hours",

    # Student total captured by StudentPointAdjustment when available.
    "student_total_points",

    "points_credited",
    "status",

    "reason",
    "category",
    "activity_name",
    "activity_date",

    "created_by_admin_id",

    "credited_at",
    "created_at",
    "updated_at",
)


# =========================================================
# 5. event_participation.csv
# =========================================================
#
# EVENT_SUBMISSION:
#     EventSubmission ownership/approval/history.
#
# EVENT_PARTICIPANT:
#     historical imported/linked participant identity snapshot.
#
# ROLE_ASSIGNMENT:
#     EventRoleAssignment operational participant/volunteer role.
#
# This preserves both current LoRaa Student participation and
# external-participant historical identity.
# =========================================================

EVENT_PARTICIPATION_RECORD_TYPES = (
    "EVENT_SUBMISSION",
    "EVENT_PARTICIPANT",
    "ROLE_ASSIGNMENT",
)


EVENT_PARTICIPATION_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "record_type",
    "record_id",

    "event_id",
    "event_title",
    "event_date",
    "event_role",
    "exclusive_group_key",

    "student_id",
    "student_usn",
    "student_name",

    "event_participant_id",

    # Historical participant snapshot.
    "participant_type",
    "participant_status",

    "name_snapshot",
    "email_snapshot",
    "phone_snapshot",
    "usn_snapshot",
    "institution_name_snapshot",

    "email_normalized",
    "phone_normalized",
    "usn_normalized",
    "institution_name_normalized",

    "source_fingerprint",
    "import_batch_id",
    "participant_created_by_admin_id",

    # Submission fields.
    "submission_status",
    "submission_description",
    "submitted_at",
    "approved_at",
    "rejection_reason",
    "awarded_points",
    "points_credited",

    # Role-assignment field.
    "role_allowed",

    "created_at",
    "linked_at",
)


# =========================================================
# 6. certificates.csv
# =========================================================

CERTIFICATES_COLUMNS = (
    *EXPORT_CONTEXT_COLUMNS,

    "certificate_id",
    "certificate_no",
    "issued_at",

    "student_id",
    "student_usn",
    "student_name",

    "submission_id",

    "event_id",
    "event_title",

    "activity_type_id",
    "activity_type_name",

    # Stored object reference only. Never embed PDF bytes in CSV.
    "pdf_path",

    "revoked_at",
    "revoke_reason",
)


# =========================================================
# ZIP CONTRACT
# =========================================================

BATCH_EXPORT_CSV_SPECS = {
    "students.csv": STUDENTS_COLUMNS,
    "academic_history.csv": ACADEMIC_HISTORY_COLUMNS,
    "activity_records.csv": ACTIVITY_RECORDS_COLUMNS,
    "activity_points.csv": ACTIVITY_POINTS_COLUMNS,
    "event_participation.csv": EVENT_PARTICIPATION_COLUMNS,
    "certificates.csv": CERTIFICATES_COLUMNS,
}


BATCH_EXPORT_CSV_FILENAMES = tuple(
    BATCH_EXPORT_CSV_SPECS.keys()
)
