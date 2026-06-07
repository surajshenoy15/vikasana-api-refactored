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

import asyncio
from app.features.faculty.models import Faculty
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

def _trigger_student_welcome_email(email: str | None, name: str | None):
    if not email:
        return

    async def _send_later():
        try:
            app_url = os.getenv(
                "STUDENT_APP_DOWNLOAD_URL",
                "https://loraa-connect.vikasanafoundation.org/install",
            )
            await send_student_welcome_email(
                to_email=email,
                to_name=name or "Student",
                app_download_url=app_url,
            )
        except Exception as e:
            print(f"[WARN] Student welcome email not sent for {email}: {e}")

    try:
        asyncio.create_task(_send_later())
    except RuntimeError:
        print(f"[WARN] Could not schedule welcome email for {email}")

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
    _trigger_student_welcome_email(s.email, s.name)

    return s

async def create_students_from_csv(
    db: AsyncSession,
    csv_bytes: bytes,
    skip_duplicates: bool = True,
    *,
    faculty_college: str,
    faculty_id: int | None = None,
    allow_csv_college: bool = False,
    allow_csv_faculty_email: bool = False,
) -> Tuple[int, int, int, int, List[str]]:
    """
    Faculty upload CSV:
      name,email,usn,branch,student_type,admitted_year,passout_year

    Admin Green Circuit upload CSV:
      name,email,usn,branch,college,student_type,admitted_year,passout_year,faculty_email
    """

    default_college = (faculty_college or "").strip()

    errors: List[str] = []
    inserted = 0
    skipped = 0
    invalid = 0
    welcome_targets: list[tuple[str, str]] = []

    try:
        text = csv_bytes.decode("utf-8-sig")
    except Exception:
        text = csv_bytes.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return (0, 0, 0, 0, ["CSV has no headers. Required: name,email,usn,branch,student_type,admitted_year,passout_year"])

    field_map, headers = _normalize_csv_headers(reader.fieldnames)

    required = {"name", "email", "usn", "branch", "passout_year", "admitted_year"}

    if allow_csv_college:
        required.add("college")

    missing = required - headers
    if missing:
        return (0, 0, 0, 0, [f"Missing headers: {', '.join(sorted(missing))}"])

    rows = list(reader)
    total_rows = len(rows)

    # Preload existing students for duplicate check
    existing_rows = (
        await db.execute(select(Student.college, Student.usn, Student.email))
    ).all()

    existing_usns = {
        (str(college or "").strip().lower(), str(usn or "").strip().upper())
        for college, usn, email in existing_rows
        if college and usn
    }

    existing_emails = {
        (str(college or "").strip().lower(), str(email or "").strip().lower())
        for college, usn, email in existing_rows
        if college and email
    }

    # Preload faculty by email for admin multi-college upload
    faculty_by_email = {}

    if allow_csv_faculty_email and "faculty_email" in headers:
        faculty_rows = (await db.execute(select(Faculty))).scalars().all()
        faculty_by_email = {
            str(f.email or "").strip().lower(): f
            for f in faculty_rows
            if getattr(f, "email", None)
        }

    for idx, row in enumerate(rows, start=2):
        try:
            name = _clean(row.get(field_map["name"], ""))
            email = _clean(row.get(field_map["email"], "")).lower()
            usn = _clean(row.get(field_map["usn"], "")).upper()
            branch = _clean(row.get(field_map["branch"], ""))

            passout_year = int(_clean(row.get(field_map["passout_year"], "")))
            admitted_year = int(_clean(row.get(field_map["admitted_year"], "")))

            stype_raw = ""
            if "student_type" in headers:
                stype_raw = _clean(row.get(field_map["student_type"], "")).upper()

            if allow_csv_college and "college" in headers:
                row_college = _clean(row.get(field_map["college"], ""))
            else:
                row_college = default_college

            if not row_college:
                raise ValueError("college cannot be empty")

            row_faculty_id = faculty_id

            if allow_csv_faculty_email and "faculty_email" in headers:
                faculty_email = _clean(row.get(field_map["faculty_email"], "")).lower()

                if faculty_email:
                    faculty = faculty_by_email.get(faculty_email)

                    if not faculty:
                        raise ValueError(f"Faculty email not found: {faculty_email}")

                    row_faculty_id = faculty.id

                    # If college is blank in future, use faculty college
                    if not row_college:
                        row_college = faculty.college

            if not name or not email or not usn or not branch:
                raise ValueError("name/email/usn/branch cannot be empty")

            college_key = row_college.strip().lower()
            usn_key = usn.strip().upper()
            email_key = email.strip().lower()

            dup = (
                (college_key, usn_key) in existing_usns
                or (college_key, email_key) in existing_emails
            )

            if dup:
                if skip_duplicates:
                    skipped += 1
                    continue
                raise ValueError("Duplicate USN/email in this college")

            stype = _parse_student_type(stype_raw)
            required_points = _required_points_for_type(stype)

            s = Student(
                college=row_college,
                name=name,
                usn=usn,
                branch=branch,
                email=email,
                student_type=stype,
                required_total_points=required_points,
                total_points_earned=0,
                passout_year=passout_year,
                admitted_year=admitted_year,
                created_by_faculty_id=row_faculty_id,
            )

            db.add(s)
            inserted += 1

            existing_usns.add((college_key, usn_key))
            existing_emails.add((college_key, email_key))
            welcome_targets.append((email, name))

        except Exception as e:
            invalid += 1
            errors.append(f"Row {idx}: {str(e)}")

    await db.commit()

    # Send welcome emails only for inserted rows
    for email, name in welcome_targets:
        _trigger_student_welcome_email(email, name)

    return (total_rows, inserted, skipped, invalid, errors)