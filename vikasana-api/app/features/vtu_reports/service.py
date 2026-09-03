from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.activities.models import (
    ActivityType,
    StudentActivityStats,
)
from app.features.organization.models import AcademicBatch, Department
from app.features.students.models import Student

from app.features.events.models import (
    Event,
    EventSubmission,
    EventActivityType,
)


VTU_REQUIRED_HEADS = 5
VTU_POINTS_PER_HEAD = 20
VTU_REQUIRED_TOTAL = 100


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def format_number(value: float) -> str:
    value = safe_float(value)

    if abs(value - round(value)) < 0.000001:
        return str(int(round(value)))

    return f"{value:.2f}".rstrip("0").rstrip(".")


def safe_filename(value: str) -> str:
    value = str(value or "report").strip()

    value = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        value,
    )

    return value.strip("_") or "report"


def get_student_name(student: Student) -> str:
    return str(
        getattr(student, "name", None)
        or getattr(student, "full_name", None)
        or ""
    ).strip()


def get_activity_number(activity_type: ActivityType) -> int:
    """
    Currently LoRaa ActivityType.id is treated as the official
    Activity Number.

    If we later introduce a dedicated vtu_activity_number column,
    this code will automatically use it.
    """

    explicit_number = getattr(
        activity_type,
        "vtu_activity_number",
        None,
    )

    if explicit_number is not None:
        return safe_int(
            explicit_number,
            safe_int(activity_type.id),
        )

    return safe_int(activity_type.id)


# ============================================================
# VTU STATUS LOGIC
# ============================================================

def calculate_vtu_status(
    activity_rows: list[dict[str, Any]],
) -> str:
    """
    READY
        At least 5 distinct activity types have reached 20 points.

    NEEDS_MAPPING
        Student has >= 5 activity types and >= 100 eligible raw
        points, but fewer than 5 heads individually reached 20.

    IN_PROGRESS
        Student still has insufficient activity progress.
    """

    completed_heads = sum(
        1
        for row in activity_rows
        if row["counted_points"] >= VTU_POINTS_PER_HEAD
    )

    total_eligible_points = sum(
        min(
            VTU_POINTS_PER_HEAD,
            safe_int(row["earned_points"]),
        )
        for row in activity_rows
    )

    if completed_heads >= VTU_REQUIRED_HEADS:
        return "READY"

    if (
        len(activity_rows) >= VTU_REQUIRED_HEADS
        and total_eligible_points >= VTU_REQUIRED_TOTAL
    ):
        return "NEEDS_MAPPING"

    return "IN_PROGRESS"


def choose_display_heads(
    activity_rows: list[dict[str, Any]],
    vtu_status: str,
) -> list[dict[str, Any]]:
    """
    READY:
        select five completed 20-point activity types.

    Other statuses:
        show strongest activity types for preview.

    No automatic cross-category mapping is performed here.
    """

    if vtu_status == "READY":
        candidates = [
            row
            for row in activity_rows
            if row["counted_points"] >= VTU_POINTS_PER_HEAD
        ]
    else:
        candidates = list(activity_rows)

    candidates.sort(
        key=lambda row: (
            -safe_int(row["counted_points"]),
            safe_int(row["activity_no"]),
        )
    )

    return candidates[:VTU_REQUIRED_HEADS]


# ============================================================
# SCOPE VALIDATION
# ============================================================

async def validate_department_scope(
    db: AsyncSession,
    *,
    college: str,
    department_id: int | None,
) -> Department | None:

    if department_id is None:
        return None

    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            func.lower(func.trim(Department.college))
            == college.strip().lower(),
        )
    )

    department = result.scalar_one_or_none()

    if department is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Department does not belong to "
                "the selected college"
            ),
        )

    return department


async def validate_batch_scope(
    db: AsyncSession,
    *,
    college: str,
    batch_id: int | None,
) -> AcademicBatch | None:

    if batch_id is None:
        return None

    result = await db.execute(
        select(AcademicBatch).where(
            AcademicBatch.id == batch_id,
            func.lower(func.trim(AcademicBatch.college))
            == college.strip().lower(),
        )
    )

    batch = result.scalar_one_or_none()

    if batch is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Batch does not belong to "
                "the selected college"
            ),
        )

    return batch


async def validate_report_scope(
    db: AsyncSession,
    *,
    college: str,
    department_id: int | None = None,
    batch_id: int | None = None,
) -> None:

    if not college or not college.strip():
        raise HTTPException(
            status_code=400,
            detail="College is required",
        )

    await validate_department_scope(
        db,
        college=college,
        department_id=department_id,
    )

    await validate_batch_scope(
        db,
        college=college,
        batch_id=batch_id,
    )


# ============================================================
# FILTER OPTIONS
# ============================================================

async def get_admin_colleges(
    db: AsyncSession,
) -> list[str]:

    result = await db.execute(
        select(Student.college)
        .where(
            Student.college.is_not(None),
        )
        .distinct()
        .order_by(
            Student.college.asc(),
        )
    )

    return [
        str(row[0]).strip()
        for row in result.all()
        if row[0]
    ]


async def get_college_report_options(
    db: AsyncSession,
    *,
    college: str,
) -> dict[str, Any]:

    departments_result = await db.execute(
        select(Department)
        .where(
            func.lower(func.trim(Department.college))
            == college.strip().lower(),
            Department.is_active.is_(True),
        )
        .order_by(
            Department.name.asc(),
        )
    )

    departments = [
        {
            "id": department.id,
            "name": department.name,
            "code": department.code,
        }
        for department in departments_result.scalars().all()
    ]

    batches_result = await db.execute(
        select(AcademicBatch)
        .where(
            func.lower(func.trim(AcademicBatch.college))
            == college.strip().lower(),
            AcademicBatch.is_active.is_(True),
        )
        .order_by(
            AcademicBatch.admitted_year.desc(),
        )
    )

    batches = [
        {
            "id": batch.id,
            "name": batch.name,
            "admitted_year": batch.admitted_year,
            "passout_year": batch.passout_year,
        }
        for batch in batches_result.scalars().all()
    ]

    return {
        "departments": departments,
        "batches": batches,
    }


# ============================================================
# MAIN VTU REPORT
# ============================================================

async def build_vtu_report(
    db: AsyncSession,
    *,
    college: str,
    department_id: int | None = None,
    batch_id: int | None = None,
    current_year: int | None = None,
    q: str | None = None,
    status_filter: str = "ALL",
) -> dict[str, Any]:

    await validate_report_scope(
        db,
        college=college,
        department_id=department_id,
        batch_id=batch_id,
    )

    # --------------------------------------------------------
    # STUDENTS
    # --------------------------------------------------------

    stmt = (
        select(
            Student,
            Department,
            AcademicBatch,
        )
        .outerjoin(
            Department,
            Department.id == Student.department_id,
        )
        .outerjoin(
            AcademicBatch,
            AcademicBatch.id == Student.batch_id,
        )
        .where(
            func.lower(func.trim(Student.college))
            == college.strip().lower()
        )
    )

    if hasattr(Student, "is_active"):
        stmt = stmt.where(
            Student.is_active.is_(True),
        )

    if department_id is not None:
        stmt = stmt.where(
            Student.department_id == department_id,
        )

    if batch_id is not None:
        stmt = stmt.where(
            Student.batch_id == batch_id,
        )

    if current_year is not None:
        stmt = stmt.where(
            Student.current_year == current_year,
        )

    if q and q.strip():
        search = f"%{q.strip()}%"

        stmt = stmt.where(
            or_(
                Student.name.ilike(search),
                Student.usn.ilike(search),
            )
        )

    stmt = stmt.order_by(
        Student.usn.asc(),
        Student.name.asc(),
    )

    student_rows = (
        await db.execute(stmt)
    ).all()

    if not student_rows:
        return {
            "items": [],
            "total": 0,
            "summary": {
                "students": 0,
                "ready": 0,
                "needs_mapping": 0,
                "in_progress": 0,
            },
        }

    student_ids = [
        student.id
        for student, _, _ in student_rows
    ]

    # --------------------------------------------------------
    # ACTUAL EVENT TITLES USED FOR EACH ACTIVITY TYPE
    #
    # Only submissions that really credited > 0 points are
    # considered. This prevents old zero-point/test events
    # from appearing in the official VTU report.
    # --------------------------------------------------------

    event_title_result = await db.execute(
        select(
            EventSubmission.student_id,
            EventActivityType.activity_type_id,
            Event.id.label("event_id"),
            Event.title.label("event_title"),
        )
        .join(
            Event,
            Event.id == EventSubmission.event_id,
        )
        .join(
            EventActivityType,
            EventActivityType.event_id == Event.id,
        )
        .where(
            EventSubmission.student_id.in_(student_ids),
            EventSubmission.points_credited.is_(True),
            EventSubmission.awarded_points > 0,
        )
        .order_by(
            EventSubmission.student_id.asc(),
            EventActivityType.activity_type_id.asc(),
            Event.event_date.asc().nullsfirst(),
            Event.id.asc(),
        )
    )

    event_titles_by_student_type: dict[
        tuple[int, int],
        list[str],
    ] = defaultdict(list)

    seen_event_ids: dict[
        tuple[int, int],
        set[int],
    ] = defaultdict(set)

    for row in event_title_result.all():

        key = (
            int(row.student_id),
            int(row.activity_type_id),
        )

        event_id = int(row.event_id)

        if event_id in seen_event_ids[key]:
            continue

        seen_event_ids[key].add(event_id)

        title = str(
            row.event_title or ""
        ).strip()

        if title:
            event_titles_by_student_type[
                key
            ].append(title)

    # --------------------------------------------------------
    # EXISTING LORAA CREDITED POINTS
    #
    # IMPORTANT:
    # We are NOT recalculating points here.
    #
    # StudentActivityStats is already populated by your
    # existing approval/scoring system.
    # --------------------------------------------------------

    stats_result = await db.execute(
        select(
            StudentActivityStats,
            ActivityType,
        )
        .join(
            ActivityType,
            ActivityType.id
            == StudentActivityStats.activity_type_id,
        )
        .where(
            StudentActivityStats.student_id.in_(
                student_ids
            )
        )
    )

    activity_by_student: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for stats, activity_type in stats_result.all():

        earned_points = max(
            0,
            safe_int(stats.points_awarded),
        )

        if earned_points <= 0:
            continue

        verified_hours = max(
            0.0,
            safe_float(
                stats.total_verified_hours
            ),
        )

        counted_points = min(
            VTU_POINTS_PER_HEAD,
            earned_points,
        )

        activity_type_name = str(
            activity_type.name
            or f"Activity {activity_type.id}"
        ).strip()

        event_titles = list(
            event_titles_by_student_type.get(
                (
                    int(stats.student_id),
                    int(activity_type.id),
                ),
                [],
            )
        )

        # Official VTU activity-head display:
        #
        # Multiple events under the same admin-selected
        # Activity Type are combined into one head.
        #
        # Example:
        # Blood Donation + Cleanliness Drive + Cycle Street
        #
        # If no positive credited event can be resolved,
        # fall back safely to the Activity Type name.
        display_activity_name = (
            " + ".join(event_titles)
            if event_titles
            else activity_type_name
        )

        activity_by_student[
            int(stats.student_id)
        ].append(
            {
                "activity_type_id": int(
                    activity_type.id
                ),
                "activity_no": get_activity_number(
                    activity_type
                ),

                # Generic configured Activity Type
                "activity_type_name": activity_type_name,

                # Actual LoRaa events contributing to this head
                "event_titles": event_titles,

                # What appears in the VTU Excel Activity Head
                "activity_name": display_activity_name,

                "verified_hours": verified_hours,
                "earned_points": earned_points,
                "counted_points": counted_points,
                "overflow_points": max(
                    0,
                    earned_points
                    - VTU_POINTS_PER_HEAD,
                ),
                "hours_points": (
                    f"{format_number(verified_hours)}"
                    f"({counted_points})"
                ),
            }
        )

    # --------------------------------------------------------
    # BUILD STUDENT REPORT ROWS
    # --------------------------------------------------------

    items: list[dict[str, Any]] = []

    summary = {
        "students": len(student_rows),
        "ready": 0,
        "needs_mapping": 0,
        "in_progress": 0,
    }

    for student, department, batch in student_rows:

        activity_rows = activity_by_student.get(
            int(student.id),
            [],
        )

        vtu_status = calculate_vtu_status(
            activity_rows
        )

        selected_heads = choose_display_heads(
            activity_rows,
            vtu_status,
        )

        completed_heads = sum(
            1
            for row in activity_rows
            if row["counted_points"]
            >= VTU_POINTS_PER_HEAD
        )

        eligible_points = min(
            VTU_REQUIRED_TOTAL,
            sum(
                safe_int(
                    row["counted_points"]
                )
                for row in activity_rows
            ),
        )

        raw_points = sum(
            safe_int(
                row["earned_points"]
            )
            for row in activity_rows
        )

        if vtu_status == "READY":
            summary["ready"] += 1

        elif vtu_status == "NEEDS_MAPPING":
            summary["needs_mapping"] += 1

        else:
            summary["in_progress"] += 1

        items.append(
            {
                "student_id": student.id,
                "usn": str(
                    student.usn or ""
                ).strip(),
                "name": get_student_name(
                    student
                ),
                "college": str(
                    student.college or college
                ).strip(),

                "department_id": (
                    student.department_id
                ),
                "department_name": (
                    department.name
                    if department
                    else None
                ),
                "department_code": (
                    department.code
                    if department
                    else None
                ),

                "batch_id": (
                    student.batch_id
                ),
                "batch_name": (
                    batch.name
                    if batch
                    else None
                ),

                "current_year": (
                    student.current_year
                ),

                "raw_points": raw_points,
                "eligible_points": eligible_points,

                "activity_types_completed": (
                    completed_heads
                ),

                "total_activity_types": (
                    len(activity_rows)
                ),

                "status": vtu_status,

                "heads": selected_heads,

                "all_activity_types": sorted(
                    activity_rows,
                    key=lambda row: (
                        row["activity_no"]
                    ),
                ),
            }
        )

    normalized_status = (
        str(status_filter or "ALL")
        .strip()
        .upper()
    )

    if normalized_status != "ALL":
        items = [
            item
            for item in items
            if item["status"]
            == normalized_status
        ]

    return {
        "items": items,
        "total": len(items),
        "summary": summary,
    }


# ============================================================
# EXPORT FORMAT
# ============================================================

def export_headers() -> list[str]:
    return [
        "S. No.",
        "USN",
        "Name",

        "Activity Head 1 (Name)",
        "Only Activity Number 1",
        "Hours / Points 1",

        "Activity Head 2 (Name)",
        "Only Activity Number 2",
        "Hours / Points 2",

        "Activity Head 3 (Name)",
        "Only Activity Number 3",
        "Hours / Points 3",

        "Activity Head 4 (Name)",
        "Only Activity Number 4",
        "Hours / Points 4",

        "Activity Head 5 (Name)",
        "Only Activity Number 5",
        "Hours / Points 5",

        "Total number of Activity points earned",
    ]


def flatten_student_export_row(
    student: dict[str, Any],
    serial_number: int,
) -> list[Any]:

    heads = list(
        student.get("heads") or []
    )[:VTU_REQUIRED_HEADS]

    while len(heads) < VTU_REQUIRED_HEADS:
        heads.append(None)

    row: list[Any] = [
        serial_number,
        student.get("usn", ""),
        student.get("name", ""),
    ]

    total = 0

    for head in heads:

        if head is None:
            row.extend(
                [
                    "",
                    "",
                    "",
                ]
            )
            continue

        points = safe_int(
            head.get("counted_points")
        )

        total += points

        row.extend(
            [
                head.get(
                    "activity_name",
                    "",
                ),
                head.get(
                    "activity_no",
                    "",
                ),
                head.get(
                    "hours_points",
                    "",
                ),
            ]
        )

    row.append(
        min(
            VTU_REQUIRED_TOTAL,
            total,
        )
    )

    return row


# ============================================================
# CSV EXPORT
# ============================================================

def build_csv(
    items: list[dict[str, Any]],
) -> bytes:

    stream = io.StringIO(
        newline=""
    )

    writer = csv.writer(
        stream
    )

    writer.writerow(
        export_headers()
    )

    for serial, student in enumerate(
        items,
        start=1,
    ):
        writer.writerow(
            flatten_student_export_row(
                student,
                serial,
            )
        )

    return stream.getvalue().encode(
        "utf-8-sig"
    )


# ============================================================
# XLSX EXPORT
# ============================================================

def build_xlsx(
    items: list[dict[str, Any]],
) -> bytes:

    try:
        import xlsxwriter

    except ImportError as exc:
        raise RuntimeError(
            "XlsxWriter is required for Excel export"
        ) from exc

    output = io.BytesIO()

    workbook = xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
        },
    )

    worksheet = workbook.add_worksheet(
        "Activity Points"
    )

    # --------------------------------------------------------
    # FORMATS
    # --------------------------------------------------------

    title_format = workbook.add_format(
        {
            "bold": True,
            "font_size": 15,
            "align": "center",
            "valign": "vcenter",
            "border": 1,
        }
    )

    group_format = workbook.add_format(
        {
            "bold": True,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
            "border": 1,
        }
    )

    header_format = workbook.add_format(
        {
            "bold": True,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
            "border": 1,
        }
    )

    text_format = workbook.add_format(
        {
            "valign": "top",
            "text_wrap": True,
            "border": 1,
        }
    )

    center_format = workbook.add_format(
        {
            "align": "center",
            "valign": "top",
            "text_wrap": True,
            "border": 1,
        }
    )

    total_format = workbook.add_format(
        {
            "bold": True,
            "align": "center",
            "valign": "top",
            "border": 1,
        }
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    worksheet.merge_range(
        "A1:S1",
        "Student Activity Points Record",
        title_format,
    )

    # --------------------------------------------------------
    # GROUP HEADERS - SAME STYLE AS YOUR SAMPLE
    # --------------------------------------------------------

    worksheet.merge_range(
        "A2:C2",
        "Student Details",
        group_format,
    )

    worksheet.merge_range(
        "D2:F2",
        "Activity Head 1",
        group_format,
    )

    worksheet.merge_range(
        "G2:I2",
        "Activity Head 2",
        group_format,
    )

    worksheet.merge_range(
        "J2:L2",
        "Activity Head 3",
        group_format,
    )

    worksheet.merge_range(
        "M2:O2",
        "Activity Head 4",
        group_format,
    )

    worksheet.merge_range(
        "P2:R2",
        "Activity Head 5",
        group_format,
    )

    worksheet.write(
        "S2",
        "Total Activity Points Earned",
        group_format,
    )

    # --------------------------------------------------------
    # COLUMN HEADERS
    # --------------------------------------------------------

    headers = export_headers()

    for column_index, header in enumerate(
        headers
    ):
        worksheet.write(
            2,
            column_index,
            header,
            header_format,
        )

    # --------------------------------------------------------
    # STUDENTS
    # --------------------------------------------------------

    row_index = 3

    for serial, student in enumerate(
        items,
        start=1,
    ):

        values = flatten_student_export_row(
            student,
            serial,
        )

        for column_index, value in enumerate(
            values
        ):

            if column_index in {
                0,
                4,
                5,
                7,
                8,
                10,
                11,
                13,
                14,
                16,
                17,
            }:
                cell_format = center_format

            elif column_index == 18:
                cell_format = total_format

            else:
                cell_format = text_format

            worksheet.write(
                row_index,
                column_index,
                value,
                cell_format,
            )

        row_index += 1

    # --------------------------------------------------------
    # WIDTHS
    # --------------------------------------------------------

    worksheet.set_column(
        "A:A",
        7,
    )

    worksheet.set_column(
        "B:B",
        18,
    )

    worksheet.set_column(
        "C:C",
        25,
    )

    for column in [
        "D",
        "G",
        "J",
        "M",
        "P",
    ]:
        worksheet.set_column(
            f"{column}:{column}",
            40,
        )

    for column in [
        "E",
        "H",
        "K",
        "N",
        "Q",
    ]:
        worksheet.set_column(
            f"{column}:{column}",
            14,
        )

    for column in [
        "F",
        "I",
        "L",
        "O",
        "R",
    ]:
        worksheet.set_column(
            f"{column}:{column}",
            18,
        )

    worksheet.set_column(
        "S:S",
        18,
    )

    worksheet.set_row(
        0,
        25,
    )

    worksheet.set_row(
        1,
        35,
    )

    worksheet.set_row(
        2,
        55,
    )

    worksheet.freeze_panes(
        3,
        3,
    )

    workbook.close()

    output.seek(0)

    return output.getvalue()
