from __future__ import annotations

import csv
import hashlib
import io

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.organization.service import (
    resolve_college_scope_keys,
)
from app.features.students.models import Student
from app.features.events.models import (
    EventParticipant,
    EventParticipantImportBatch,
    ExternalParticipantLinkHistory,
)


# ============================================================
# NORMALIZATION
# ============================================================


def normalize_participant_email(
    value: str | None,
) -> str | None:
    normalized = (
        str(value or "")
        .strip()
        .casefold()
    )

    return normalized or None


def normalize_participant_phone(
    value: str | None,
) -> str | None:
    """
    Keep digits only.

    Phone is NOT currently used for Student matching because
    Student has no phone/mobile column.

    It will be useful for detecting duplicate external CSV rows
    and future external participant matching.
    """

    digits = "".join(
        ch
        for ch in str(value or "")
        if ch.isdigit()
    )

    return digits or None


def normalize_participant_usn(
    value: str | None,
) -> str | None:
    normalized = (
        str(value or "")
        .strip()
        .upper()
    )

    return normalized or None


def normalize_participant_institution(
    value: str | None,
) -> str | None:
    normalized = (
        str(value or "")
        .strip()
        .casefold()
    )

    return normalized or None



# ============================================================
# CSV PARSING
# ============================================================


def build_participant_source_fingerprint(
    *,
    name: str | None,
    email_normalized: str | None,
    phone_normalized: str | None,
    usn_normalized: str | None,
    institution_name_normalized: str | None,
) -> str:
    """
    Build a deterministic SHA-256 fingerprint for one normalized
    imported participant row.

    The fingerprint is used only to make repeated Admin imports
    idempotent within the same Event.
    """

    normalized_name = (
        str(name or "")
        .strip()
        .casefold()
    )

    payload = "\x1f".join(
        (
            normalized_name,
            email_normalized or "",
            phone_normalized or "",
            usn_normalized or "",
            institution_name_normalized or "",
        )
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()




REQUIRED_PARTICIPANT_CSV_HEADERS = {
    "name",
    "email",
    "phone",
    "usn",
    "college",
}


def _normalize_csv_headers(
    fieldnames: list[str] | None,
) -> tuple[dict[str, str], set[str]]:
    if not fieldnames:
        return {}, set()

    field_map = {
        str(header).strip().casefold(): header
        for header in fieldnames
        if header and str(header).strip()
    }

    return field_map, set(field_map)


def parse_event_participant_csv(
    csv_bytes: bytes,
) -> dict[str, Any]:
    """
    Parse an Admin-supplied event participant CSV.

    Required headers:
        name,email,phone,usn,college

    Row requirements:
    - name is required
    - college is required
    - at least one of email / phone / usn is required

    This helper performs NO database writes and NO Student matching.
    """

    try:
        decoded = csv_bytes.decode(
            "utf-8-sig"
        )
    except Exception:
        decoded = csv_bytes.decode(
            "utf-8",
            errors="replace",
        )

    reader = csv.DictReader(
        io.StringIO(decoded)
    )

    if not reader.fieldnames:
        return {
            "ok": False,
            "total_rows": 0,
            "valid_rows": [],
            "duplicate_rows": [],
            "invalid_rows": [],
            "errors": [
                (
                    "CSV has no headers. Required: "
                    "name,email,phone,usn,college"
                )
            ],
        }

    field_map, headers = (
        _normalize_csv_headers(
            reader.fieldnames
        )
    )

    missing_headers = (
        REQUIRED_PARTICIPANT_CSV_HEADERS
        - headers
    )

    if missing_headers:
        return {
            "ok": False,
            "total_rows": 0,
            "valid_rows": [],
            "duplicate_rows": [],
            "invalid_rows": [],
            "errors": [
                (
                    "Missing headers: "
                    + ", ".join(
                        sorted(missing_headers)
                    )
                )
            ],
        }

    source_rows = list(reader)

    valid_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    # Exact normalized CSV-row fingerprints already accepted.
    #
    # This is intentionally conservative. Identity conflicts such
    # as a shared email/phone are handled later by the matcher as
    # MATCHED / AMBIGUOUS rather than being silently discarded here.
    seen_row_fingerprints: dict[
        tuple[
            str,
            str | None,
            str | None,
            str | None,
            str | None,
        ],
        int,
    ] = {}

    for row_number, row in enumerate(
        source_rows,
        start=2,
    ):
        name = str(
            row.get(
                field_map["name"],
                "",
            )
            or ""
        ).strip()

        email_snapshot = str(
            row.get(
                field_map["email"],
                "",
            )
            or ""
        ).strip()

        phone_snapshot = str(
            row.get(
                field_map["phone"],
                "",
            )
            or ""
        ).strip()

        usn_snapshot = str(
            row.get(
                field_map["usn"],
                "",
            )
            or ""
        ).strip()

        institution_snapshot = str(
            row.get(
                field_map["college"],
                "",
            )
            or ""
        ).strip()

        row_errors: list[str] = []

        if not name:
            row_errors.append(
                "name cannot be empty"
            )

        if not institution_snapshot:
            row_errors.append(
                "college cannot be empty"
            )

        if not any(
            (
                email_snapshot,
                phone_snapshot,
                usn_snapshot,
            )
        ):
            row_errors.append(
                (
                    "at least one of email, "
                    "phone or usn is required"
                )
            )

        parsed_row = {
            "row_number": row_number,

            # Historical snapshots.
            "name_snapshot": name,
            "email_snapshot": (
                email_snapshot or None
            ),
            "phone_snapshot": (
                phone_snapshot or None
            ),
            "usn_snapshot": (
                usn_snapshot or None
            ),
            "institution_name_snapshot": (
                institution_snapshot
                or None
            ),

            # Matching/search values.
            "email_normalized": (
                normalize_participant_email(
                    email_snapshot
                )
            ),
            "phone_normalized": (
                normalize_participant_phone(
                    phone_snapshot
                )
            ),
            "usn_normalized": (
                normalize_participant_usn(
                    usn_snapshot
                )
            ),
            "institution_name_normalized": (
                normalize_participant_institution(
                    institution_snapshot
                )
            ),
        }

        parsed_row["source_fingerprint"] = (
            build_participant_source_fingerprint(
                name=name,
                email_normalized=parsed_row[
                    "email_normalized"
                ],
                phone_normalized=parsed_row[
                    "phone_normalized"
                ],
                usn_normalized=parsed_row[
                    "usn_normalized"
                ],
                institution_name_normalized=parsed_row[
                    "institution_name_normalized"
                ],
            )
        )

        if row_errors:
            invalid_rows.append(
                {
                    **parsed_row,
                    "errors": row_errors,
                }
            )
            continue

        row_fingerprint = (
            name.casefold(),
            parsed_row["email_normalized"],
            parsed_row["phone_normalized"],
            parsed_row["usn_normalized"],
            parsed_row[
                "institution_name_normalized"
            ],
        )

        first_row_number = (
            seen_row_fingerprints.get(
                row_fingerprint
            )
        )

        if first_row_number is not None:
            duplicate_rows.append(
                {
                    **parsed_row,
                    "duplicate_of_row": (
                        first_row_number
                    ),
                }
            )
            continue

        seen_row_fingerprints[
            row_fingerprint
        ] = row_number

        valid_rows.append(
            parsed_row
        )

    return {
        "ok": True,
        "total_rows": len(source_rows),
        "valid_rows": valid_rows,
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,
        "errors": [],
    }


# ============================================================
# MATCH RESULT
# ============================================================


def _match_result(
    *,
    status: str,
    student: Student | None = None,
    matched_by: str | None = None,
    candidate_count: int = 0,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "student": student,
        "student_id": (
            int(student.id)
            if student is not None
            else None
        ),
        "matched_by": matched_by,
        "candidate_count": int(candidate_count),
        "reason": reason,
    }


# ============================================================
# EXISTING LORAA STUDENT MATCHER
# ============================================================


def _resolve_student_match_candidates(
    *,
    email_candidates: list[Student],
    usn_college_candidates: list[Student],
) -> dict[str, Any]:
    """
    Resolve already-loaded strong-identifier candidates.

    Safety rule:
    conflicting strong identifiers must NEVER auto-link.
    """

    if len(email_candidates) > 1:
        return _match_result(
            status="AMBIGUOUS",
            matched_by="EMAIL",
            candidate_count=len(
                email_candidates
            ),
            reason=(
                "Multiple active Students share "
                "the normalized email"
            ),
        )

    if len(usn_college_candidates) > 1:
        return _match_result(
            status="AMBIGUOUS",
            matched_by="USN_COLLEGE",
            candidate_count=len(
                usn_college_candidates
            ),
            reason=(
                "Multiple active Students matched "
                "USN plus college/alias scope"
            ),
        )

    email_student = (
        email_candidates[0]
        if len(email_candidates) == 1
        else None
    )

    usn_student = (
        usn_college_candidates[0]
        if len(usn_college_candidates) == 1
        else None
    )

    # Both strong identifiers matched, but to different people.
    # Never choose one identifier over the other.
    if (
        email_student is not None
        and usn_student is not None
        and int(email_student.id)
        != int(usn_student.id)
    ):
        return _match_result(
            status="AMBIGUOUS",
            matched_by="IDENTIFIER_CONFLICT",
            candidate_count=2,
            reason=(
                "Email and USN plus college matched "
                "different active Students"
            ),
        )

    if (
        email_student is not None
        and usn_student is not None
    ):
        return _match_result(
            status="MATCHED",
            student=email_student,
            matched_by="EMAIL_USN_COLLEGE",
            candidate_count=1,
            reason=(
                "Email and USN plus college both matched "
                "the same active Student"
            ),
        )

    if email_student is not None:
        return _match_result(
            status="MATCHED",
            student=email_student,
            matched_by="EMAIL",
            candidate_count=1,
            reason=(
                "Exactly one active Student matched "
                "the normalized email"
            ),
        )

    if usn_student is not None:
        return _match_result(
            status="MATCHED",
            student=usn_student,
            matched_by="USN_COLLEGE",
            candidate_count=1,
            reason=(
                "Exactly one active Student matched "
                "USN plus college/alias scope"
            ),
        )

    return _match_result(
        status="NO_MATCH",
        candidate_count=0,
        reason=(
            "No safe active Student match found. "
            "Name-only matching is not allowed."
        ),
    )


async def match_existing_student_for_event_participant(
    db: AsyncSession,
    *,
    email: str | None,
    usn: str | None,
    institution_name: str | None,
) -> dict[str, Any]:
    """
    Safely match an imported participant to an ACTIVE LoRaa Student.

    Strong identifiers:
    1. exact normalized email
    2. exact normalized USN + canonical/alias college scope

    Both identifiers are evaluated before an automatic link is made.

    Important:
    - conflicting strong identifiers => AMBIGUOUS
    - multiple candidates => AMBIGUOUS
    - name is NEVER used
    - phone is unavailable on Student
    - inactive Students are excluded
    """

    email_key = normalize_participant_email(
        email
    )

    usn_key = normalize_participant_usn(
        usn
    )

    institution_key = normalize_participant_institution(
        institution_name
    )

    email_candidates: list[Student] = []
    usn_college_candidates: list[Student] = []

    # --------------------------------------------------------
    # EMAIL CANDIDATES
    # --------------------------------------------------------

    if email_key:
        email_result = await db.execute(
            select(Student)
            .where(
                Student.is_active.is_(True),
                func.lower(
                    func.trim(Student.email)
                ) == email_key,
            )
            .order_by(Student.id.asc())
        )

        email_candidates = list(
            email_result.scalars().all()
        )

    # --------------------------------------------------------
    # USN + COLLEGE/ALIAS CANDIDATES
    # --------------------------------------------------------

    if usn_key and institution_key:
        college_scope = await resolve_college_scope_keys(
            db,
            institution_name,
        )

        if college_scope:
            usn_result = await db.execute(
                select(Student)
                .where(
                    Student.is_active.is_(True),
                    func.upper(
                        func.trim(Student.usn)
                    ) == usn_key,
                    func.lower(
                        func.trim(Student.college)
                    ).in_(
                        sorted(college_scope)
                    ),
                )
                .order_by(Student.id.asc())
            )

            usn_college_candidates = list(
                usn_result.scalars().all()
            )

    return _resolve_student_match_candidates(
        email_candidates=email_candidates,
        usn_college_candidates=(
            usn_college_candidates
        ),
    )



# ============================================================
# READ-ONLY IMPORT CLASSIFICATION
# ============================================================


async def classify_event_participant_rows(
    db: AsyncSession,
    *,
    parsed_csv: dict[str, Any],
) -> dict[str, Any]:
    """
    Classify already-parsed CSV rows without writing to the database.

    Results:
    - MATCHED   -> safe existing active LoRaa Student
    - AMBIGUOUS -> multiple Student candidates
    - EXTERNAL  -> no safe Student match

    Duplicate and invalid CSV rows are carried forward separately.
    """

    matched_rows: list[dict[str, Any]] = []
    ambiguous_rows: list[dict[str, Any]] = []
    external_rows: list[dict[str, Any]] = []

    duplicate_rows = list(
        parsed_csv.get(
            "duplicate_rows",
            [],
        )
    )

    # One LoRaa Student must represent only one participant
    # identity for a given Event.
    #
    # Different CSV rows can still resolve to the same Student
    # even when their normalized row fingerprints differ.
    matched_student_first_row: dict[int, int] = {}

    for row in parsed_csv.get(
        "valid_rows",
        [],
    ):
        match = (
            await match_existing_student_for_event_participant(
                db,
                email=row.get(
                    "email_snapshot"
                ),
                usn=row.get(
                    "usn_snapshot"
                ),
                institution_name=row.get(
                    "institution_name_snapshot"
                ),
            )
        )

        match_status = str(
            match.get("status") or ""
        ).upper()

        result_row = {
            **row,
            "match_status": match_status,
            "student_id": match.get(
                "student_id"
            ),
            "matched_by": match.get(
                "matched_by"
            ),
            "candidate_count": int(
                match.get(
                    "candidate_count",
                    0,
                )
                or 0
            ),
            "match_reason": match.get(
                "reason"
            ),
        }

        if match_status == "MATCHED":
            student_id = result_row.get(
                "student_id"
            )

            if student_id is None:
                ambiguous_rows.append(
                    {
                        **result_row,
                        "match_status": "AMBIGUOUS",
                        "match_reason": (
                            "Student matcher returned MATCHED "
                            "without a student_id"
                        ),
                    }
                )
                continue

            student_id = int(
                student_id
            )

            first_row_number = (
                matched_student_first_row.get(
                    student_id
                )
            )

            if first_row_number is not None:
                duplicate_rows.append(
                    {
                        **result_row,
                        "duplicate_of_row": (
                            first_row_number
                        ),
                        "duplicate_reason": (
                            "SAME_STUDENT_MATCH"
                        ),
                    }
                )
                continue

            matched_student_first_row[
                student_id
            ] = int(
                row.get(
                    "row_number",
                    0,
                )
                or 0
            )

            matched_rows.append(
                result_row
            )
            continue

        if match_status == "AMBIGUOUS":
            ambiguous_rows.append(
                result_row
            )
            continue

        external_rows.append(
            {
                **result_row,
                "match_status": "EXTERNAL",
            }
        )

    invalid_rows = list(
        parsed_csv.get(
            "invalid_rows",
            [],
        )
    )

    total_rows = int(
        parsed_csv.get(
            "total_rows",
            0,
        )
        or 0
    )

    accounted_rows = (
        len(matched_rows)
        + len(external_rows)
        + len(ambiguous_rows)
        + len(duplicate_rows)
        + len(invalid_rows)
    )

    if accounted_rows != total_rows:
        raise ValueError(
            "Participant CSV classification accounting mismatch: "
            f"total_rows={total_rows}, "
            f"accounted_rows={accounted_rows}"
        )

    return {
        "ok": bool(
            parsed_csv.get("ok")
        ),
        "total_rows": total_rows,
        "matched_rows": matched_rows,
        "external_rows": external_rows,
        "ambiguous_rows": ambiguous_rows,
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,

        "summary": {
            "matched_existing_students": len(
                matched_rows
            ),
            "external_candidates": len(
                external_rows
            ),
            "ambiguous": len(
                ambiguous_rows
            ),
            "duplicate_csv_rows": len(
                duplicate_rows
            ),
            "invalid": len(
                invalid_rows
            ),
        },
    }


# ============================================================
# PERSISTENCE PREPARATION
# ============================================================


def prepare_event_participant_records(
    *,
    classified: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Convert read-only classification results into normalized
    EventParticipant record payloads.

    This helper performs NO database writes.

    States:
    - matched existing Student:
        participant_type=LORAA_STUDENT
        status=LINKED
        student_id=<Student.id>

    - safe external participant:
        participant_type=EXTERNAL
        status=ACTIVE
        student_id=None

    - ambiguous participant:
        participant_type=EXTERNAL
        status=REVIEW_REQUIRED
        student_id=None

    Duplicate and invalid CSV rows are intentionally excluded.
    """

    records: list[dict[str, Any]] = []

    def build_base_record(
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "name_snapshot": row.get(
                "name_snapshot"
            ),
            "email_snapshot": row.get(
                "email_snapshot"
            ),
            "phone_snapshot": row.get(
                "phone_snapshot"
            ),
            "usn_snapshot": row.get(
                "usn_snapshot"
            ),
            "institution_name_snapshot": row.get(
                "institution_name_snapshot"
            ),
            "email_normalized": row.get(
                "email_normalized"
            ),
            "phone_normalized": row.get(
                "phone_normalized"
            ),
            "usn_normalized": row.get(
                "usn_normalized"
            ),
            "institution_name_normalized": row.get(
                "institution_name_normalized"
            ),
            "source_fingerprint": row.get(
                "source_fingerprint"
            ),
            "source_row_number": row.get(
                "row_number"
            ),
        }

    for row in classified.get(
        "matched_rows",
        [],
    ):
        student_id = row.get(
            "student_id"
        )

        if student_id is None:
            raise ValueError(
                "MATCHED participant is missing student_id"
            )

        records.append(
            {
                **build_base_record(row),
                "student_id": int(
                    student_id
                ),
                "participant_type": (
                    "LORAA_STUDENT"
                ),
                "status": "LINKED",
                "matched_by": row.get(
                    "matched_by"
                ),
            }
        )

    for row in classified.get(
        "external_rows",
        [],
    ):
        records.append(
            {
                **build_base_record(row),
                "student_id": None,
                "participant_type": (
                    "EXTERNAL"
                ),
                "status": "ACTIVE",
                "matched_by": None,
            }
        )

    for row in classified.get(
        "ambiguous_rows",
        [],
    ):
        records.append(
            {
                **build_base_record(row),
                "student_id": None,
                "participant_type": (
                    "EXTERNAL"
                ),
                "status": (
                    "REVIEW_REQUIRED"
                ),
                "matched_by": row.get(
                    "matched_by"
                ),
            }
        )

    return records



# ============================================================
# TRANSACTIONAL IMPORT PERSISTENCE
# ============================================================


async def persist_event_participant_import(
    db: AsyncSession,
    *,
    event_id: int,
    original_filename: str | None,
    created_by_admin_id: int | None,
    classified: dict[str, Any],
    commit: bool = True,
) -> dict[str, Any]:
    """
    Persist one already-classified Admin participant CSV import.

    The caller must validate that the Event exists before calling.

    Important:
    - No Student rows are created.
    - Existing matched Students are linked only by student_id.
    - Ambiguous rows are NEVER auto-linked.
    - Invalid CSV rows are never persisted as participants.
    - CSV duplicates are never persisted as participants.
    - Existing DB participant identities are treated idempotently.
    - commit=True commits and refreshes the import batch here.
    - commit=False flushes only; the caller owns the final
      commit/rollback so business rows and immutable audit history
      can be committed atomically.
    """

    records = prepare_event_participant_records(
        classified=classified
    )

    parsed_duplicate_count = len(
        classified.get(
            "duplicate_rows",
            [],
        )
    )

    invalid_count = len(
        classified.get(
            "invalid_rows",
            [],
        )
    )

    batch = EventParticipantImportBatch(
        event_id=int(event_id),
        original_filename=(
            str(original_filename).strip()
            if original_filename
            else None
        ),
        status="PROCESSING",
        total_rows=int(
            classified.get(
                "total_rows",
                0,
            )
            or 0
        ),
        linked_students=0,
        external_created=0,
        duplicate_rows=parsed_duplicate_count,
        ambiguous_rows=0,
        invalid_rows=invalid_count,
        created_by_admin_id=(
            int(created_by_admin_id)
            if created_by_admin_id is not None
            else None
        ),
    )

    db.add(batch)

    try:
        # Obtain import_batch_id before participant inserts.
        await db.flush()

        linked_students = 0
        external_created = 0
        ambiguous_created = 0
        database_duplicates = 0

        created_participant_ids: list[int] = []

        for record in records:
            fingerprint = record.get(
                "source_fingerprint"
            )

            student_id = record.get(
                "student_id"
            )

            # ------------------------------------------------
            # DATABASE IDEMPOTENCY:
            # SAME EVENT + SAME NORMALIZED SOURCE ROW
            # ------------------------------------------------

            if fingerprint:
                existing_fingerprint_result = await db.execute(
                    select(EventParticipant.id)
                    .where(
                        EventParticipant.event_id
                        == int(event_id),
                        EventParticipant.source_fingerprint
                        == fingerprint,
                    )
                    .limit(1)
                )

                if (
                    existing_fingerprint_result.scalar_one_or_none()
                    is not None
                ):
                    database_duplicates += 1
                    continue

            # ------------------------------------------------
            # DATABASE IDEMPOTENCY:
            # SAME EVENT + SAME EXISTING LORAA STUDENT
            # ------------------------------------------------

            if student_id is not None:
                existing_student_result = await db.execute(
                    select(EventParticipant.id)
                    .where(
                        EventParticipant.event_id
                        == int(event_id),
                        EventParticipant.student_id
                        == int(student_id),
                    )
                    .limit(1)
                )

                if (
                    existing_student_result.scalar_one_or_none()
                    is not None
                ):
                    database_duplicates += 1
                    continue

            participant_status = str(
                record.get("status") or ""
            ).upper()

            participant_type = str(
                record.get("participant_type") or ""
            ).upper()

            participant = EventParticipant(
                event_id=int(event_id),
                student_id=(
                    int(student_id)
                    if student_id is not None
                    else None
                ),

                name_snapshot=record.get(
                    "name_snapshot"
                ),
                email_snapshot=record.get(
                    "email_snapshot"
                ),
                phone_snapshot=record.get(
                    "phone_snapshot"
                ),
                usn_snapshot=record.get(
                    "usn_snapshot"
                ),
                institution_name_snapshot=record.get(
                    "institution_name_snapshot"
                ),

                email_normalized=record.get(
                    "email_normalized"
                ),
                phone_normalized=record.get(
                    "phone_normalized"
                ),
                usn_normalized=record.get(
                    "usn_normalized"
                ),
                institution_name_normalized=record.get(
                    "institution_name_normalized"
                ),
                source_fingerprint=fingerprint,

                participant_type=participant_type,
                status=participant_status,

                import_batch_id=int(batch.id),
                created_by_admin_id=(
                    int(created_by_admin_id)
                    if created_by_admin_id is not None
                    else None
                ),

                linked_at=(
                    datetime.now(timezone.utc)
                    if (
                        participant_status == "LINKED"
                        and student_id is not None
                    )
                    else None
                ),
            )

            db.add(participant)
            await db.flush()

            created_participant_ids.append(
                int(participant.id)
            )

            if (
                participant_type == "LORAA_STUDENT"
                and participant_status == "LINKED"
                and student_id is not None
            ):
                matched_by = str(
                    record.get(
                        "matched_by"
                    )
                    or ""
                ).strip().upper()

                if not matched_by:
                    raise ValueError(
                        "Linked LoRaa Student participant "
                        "is missing matched_by provenance"
                    )

                # Initial automatic linkage is also historical
                # linkage provenance and must be append-only.
                db.add(
                    ExternalParticipantLinkHistory(
                        event_participant_id=int(
                            participant.id
                        ),
                        previous_student_id=None,
                        student_id=int(
                            student_id
                        ),
                        matched_by=matched_by,
                        linked_by_admin_id=(
                            int(created_by_admin_id)
                            if created_by_admin_id
                            is not None
                            else None
                        ),
                        status="LINKED",
                        metadata_json={
                            "source": (
                                "ADMIN_PARTICIPANT_CSV_IMPORT"
                            ),
                            "import_batch_id": int(
                                batch.id
                            ),
                            "source_row_number": (
                                record.get(
                                    "source_row_number"
                                )
                            ),
                            "event_id": int(
                                event_id
                            ),
                        },
                    )
                )

                # Flush the immutable history row inside the same
                # business transaction as the participant.
                await db.flush()

                linked_students += 1

            elif (
                participant_type == "EXTERNAL"
                and participant_status == "REVIEW_REQUIRED"
            ):
                ambiguous_created += 1

            elif participant_type == "EXTERNAL":
                external_created += 1

        final_duplicate_count = (
            parsed_duplicate_count
            + database_duplicates
        )

        persisted_accounted_rows = (
            linked_students
            + external_created
            + ambiguous_created
            + final_duplicate_count
            + invalid_count
        )

        expected_total_rows = int(
            batch.total_rows or 0
        )

        if (
            persisted_accounted_rows
            != expected_total_rows
        ):
            raise ValueError(
                "Participant import persistence accounting mismatch: "
                f"total_rows={expected_total_rows}, "
                f"accounted_rows={persisted_accounted_rows}"
            )

        batch.linked_students = linked_students
        batch.external_created = external_created
        batch.ambiguous_rows = ambiguous_created
        batch.duplicate_rows = (
            final_duplicate_count
        )
        batch.invalid_rows = invalid_count
        batch.status = "COMPLETED"
        batch.completed_at = datetime.now(
            timezone.utc
        )

        if commit:
            await db.commit()
            await db.refresh(batch)
        else:
            # Caller will append related audit history and commit
            # both the business rows and audit row atomically.
            await db.flush()

        return {
            "batch_id": int(batch.id),
            "event_id": int(event_id),
            "status": batch.status,
            "total_rows": int(batch.total_rows),
            "linked_students": int(
                batch.linked_students
            ),
            "external_created": int(
                batch.external_created
            ),
            "ambiguous_rows": int(
                batch.ambiguous_rows
            ),
            "duplicate_rows": int(
                batch.duplicate_rows
            ),
            "invalid_rows": int(
                batch.invalid_rows
            ),
            "database_duplicates": int(
                database_duplicates
            ),
            "created_participant_ids": (
                created_participant_ids
            ),
        }

    except Exception:
        await db.rollback()
        raise
