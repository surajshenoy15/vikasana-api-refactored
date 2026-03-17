# app/controllers/student_controller.py
# ✅ Fully updated controller (safe + consistent with your updated routes)
# - Fixes the wrong call pattern you had in routes (current_faculty=...) by keeping signature as (faculty_college, faculty_id)
# - Adds small robustness: safe email normalization, handles missing email header properly, avoids "row.get('')" edge

import os
import csv
import io
from typing import List, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.students.models import Student, StudentType
from app.features.students.schemas.student import StudentCreate
from app.core.email_service import send_student_welcome_email


def _clean(v: str) -> str:
    return (v or "").strip()


def _parse_student_type(v: str) -> StudentType:
    x = (v or "").strip().upper()
    if x in ("DIPLOMA", "DIPLOMA_SCHEME", "DIPLOMA SCHEME"):
        return StudentType.DIPLOMA
    return StudentType.REGULAR


def _required_points_for_type(stype: StudentType) -> int:
    return 60 if stype == StudentType.DIPLOMA else 100


def _coerce_student_type(v) -> StudentType:
    """
    Accepts: StudentType enum OR string like 'REGULAR'/'DIPLOMA'
    """
    if isinstance(v, StudentType):
        return v
    return _parse_student_type(str(v))


def _normalize_csv_headers(fieldnames: list[str] | None) -> tuple[dict[str, str], set[str]]:
    """
    Returns:
      field_map: normalized_lower_header -> original_header
      headers: set of normalized_lower_header
    """
    if not fieldnames:
        return {}, set()
    field_map = {h.strip().lower(): h for h in fieldnames if h and h.strip()}
    return field_map, set(field_map.keys())


async def create_student(
    db: AsyncSession,
    payload: StudentCreate,
    *,
    faculty_college: str,
    faculty_id: int | None = None,  # ✅ mentor id
) -> Student:
    faculty_college = (faculty_college or "").strip()
    if not faculty_college:
        raise ValueError("Faculty college is missing. Please set faculty.college.")

    usn = payload.usn.strip()
    email = str(payload.email).strip().lower() if payload.email else None

    # ✅ Duplicate check within same college (USN or Email)
    dup_stmt = select(Student).where(
        Student.college == faculty_college,
        or_(
            Student.usn == usn,
            Student.email == email if email else False,
        ),
    )
    existing = (await db.execute(dup_stmt)).scalar_one_or_none()
    if existing:
        if existing.usn == usn:
            raise ValueError(f"Duplicate USN in this college: {usn}")
        raise ValueError(f"Duplicate Email in this college: {email}")

    stype = _coerce_student_type(payload.student_type)
    required_points = _required_points_for_type(stype)

    s = Student(
        college=faculty_college,  # ✅ enforced
        name=payload.name.strip(),
        usn=usn,
        branch=payload.branch.strip(),
        email=email,
        student_type=stype,

        # ✅ Activity Tracker fields
        required_total_points=required_points,
        total_points_earned=0,

        passout_year=payload.passout_year,
        admitted_year=payload.admitted_year,

        # ✅ Mentor
        created_by_faculty_id=faculty_id,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)

    # ✅ Welcome email (non-blocking)
    if s.email:
        try:
            app_url = os.getenv("STUDENT_APP_DOWNLOAD_URL", "https://vikasana.org/app")
            await send_student_welcome_email(
                to_email=s.email,
                to_name=s.name,
                app_download_url=app_url,
            )
        except Exception as e:
            print(f"[WARN] Student welcome email not sent for {s.email}: {e}")

    return s


async def create_students_from_csv(
    db: AsyncSession,
    csv_bytes: bytes,
    skip_duplicates: bool = True,
    *,
    faculty_college: str,
    faculty_id: int | None = None,  # ✅ mentor id
) -> Tuple[int, int, int, int, List[str]]:
    """
    CSV headers expected (required):
      name, usn, branch, passout_year, admitted_year
    Optional:
      email, student_type

    ✅ Headers are case-insensitive (Email/email/EMAIL supported).
    ✅ created_by_faculty_id is set for mentor name in UI.
    """
    faculty_college = (faculty_college or "").strip()
    if not faculty_college:
        return (0, 0, 0, 0, ["Faculty college is missing. Please set faculty.college."])

    errors: List[str] = []
    inserted = 0
    skipped = 0
    invalid = 0

    try:
        text = csv_bytes.decode("utf-8-sig")
    except Exception:
        text = csv_bytes.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return (0, 0, 0, 0, ["CSV has no headers. Required: name, usn, branch, passout_year, admitted_year"])

    field_map, headers = _normalize_csv_headers(reader.fieldnames)

    required = {"name", "usn", "branch", "passout_year", "admitted_year"}
    missing = required - headers
    if missing:
        return (0, 0, 0, 0, [f"Missing headers: {', '.join(sorted(missing))}"])

    rows = list(reader)
    total_rows = len(rows)

    # ✅ preload existing USNs/emails for this college
    existing_rows = (
        await db.execute(select(Student.usn, Student.email).where(Student.college == faculty_college))
    ).all()
    existing_usns = {r[0] for r in existing_rows if r[0]}
    existing_emails = {str(r[1]).lower() for r in existing_rows if r[1]}

    for idx, row in enumerate(rows, start=2):
        try:
            name = _clean(row.get(field_map["name"], ""))
            usn = _clean(row.get(field_map["usn"], ""))
            branch = _clean(row.get(field_map["branch"], ""))

            passout_year = int(_clean(row.get(field_map["passout_year"], "")))
            admitted_year = int(_clean(row.get(field_map["admitted_year"], "")))

            # Optional fields
            email = ""
            if "email" in headers:
                email = _clean(row.get(field_map["email"], "")).lower()

            stype_raw = ""
            if "student_type" in headers:
                stype_raw = _clean(row.get(field_map["student_type"], "")).upper()

            if not name or not usn or not branch:
                raise ValueError("name/usn/branch cannot be empty")

            # ✅ duplicates within same college
            dup = (usn in existing_usns) or (email and email in existing_emails)
            if dup:
                if skip_duplicates:
                    skipped += 1
                    continue
                raise ValueError("Duplicate USN/email in this college")

            stype = _parse_student_type(stype_raw)
            required_points = _required_points_for_type(stype)

            s = Student(
                college=faculty_college,  # ✅ enforced
                name=name,
                usn=usn,
                branch=branch,
                email=email or None,
                student_type=stype,

                # ✅ Activity Tracker fields
                required_total_points=required_points,
                total_points_earned=0,

                passout_year=passout_year,
                admitted_year=admitted_year,

                # ✅ Mentor
                created_by_faculty_id=faculty_id,
            )
            db.add(s)
            inserted += 1

            # update sets
            existing_usns.add(usn)
            if email:
                existing_emails.add(email)

        except Exception as e:
            invalid += 1
            errors.append(f"Row {idx}: {str(e)}")

    await db.commit()
    return (total_rows, inserted, skipped, invalid, errors)