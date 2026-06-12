# app/features/students/service.py

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


def _is_active_student(s: Student) -> bool:
    return bool(getattr(s, "is_active", True))


def _reactivate_student(
    s: Student,
    *,
    college: str,
    name: str,
    email: str,
    usn: str,
    branch: str,
    student_type: StudentType,
    passout_year: int,
    admitted_year: int,
    faculty_id: int | None,
) -> Student:
    """
    Permanent fix:
    If admin previously deleted/deactivated a student, CSV/manual add should restore
    that same row instead of creating duplicate or skipping it.
    """
    s.is_active = True
    s.college = college
    s.name = name
    s.email = email
    s.usn = usn
    s.branch = branch
    s.student_type = student_type
    s.required_total_points = _required_points_for_type(student_type)
    s.passout_year = passout_year
    s.admitted_year = admitted_year
    s.created_by_faculty_id = faculty_id

    # Do NOT reset earned points/certificates here.
    # Old activity/certificate history remains attached to the same student id.
    return s


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
    if isinstance(v, StudentType):
        return v
    return _parse_student_type(str(v))


def _normalize_csv_headers(fieldnames: list[str] | None) -> tuple[dict[str, str], set[str]]:
    if not fieldnames:
        return {}, set()

    field_map = {h.strip().lower(): h for h in fieldnames if h and h.strip()}
    return field_map, set(field_map.keys())


async def create_student(
    db: AsyncSession,
    payload: StudentCreate,
    *,
    faculty_college: str,
    faculty_id: int | None = None,
) -> Student:
    faculty_college = (faculty_college or "").strip()

    if not faculty_college:
        raise ValueError("Faculty college is missing. Please set faculty.college.")

    name = payload.name.strip()
    usn = payload.usn.strip().upper()
    branch = payload.branch.strip()
    email = str(payload.email).strip().lower() if payload.email else None
    stype = _coerce_student_type(payload.student_type)
    required_points = _required_points_for_type(stype)

    dup_stmt = (
        select(Student)
        .where(
            Student.college == faculty_college,
            or_(
                Student.usn == usn,
                Student.email == email if email else False,
            ),
        )
        .order_by(Student.id.asc())
    )

    existing_students = (await db.execute(dup_stmt)).scalars().all()

    active_existing = next((s for s in existing_students if _is_active_student(s)), None)
    inactive_existing = next((s for s in existing_students if not _is_active_student(s)), None)

    if active_existing:
        if active_existing.usn == usn:
            raise ValueError(f"Duplicate USN in this college: {usn}")
        raise ValueError(f"Duplicate Email in this college: {email}")

    # ✅ Permanent fix: restore deleted/deactivated student
    if inactive_existing:
        _reactivate_student(
            inactive_existing,
            college=faculty_college,
            name=name,
            email=email,
            usn=usn,
            branch=branch,
            student_type=stype,
            passout_year=payload.passout_year,
            admitted_year=payload.admitted_year,
            faculty_id=faculty_id,
        )

        await db.commit()
        await db.refresh(inactive_existing)

        _trigger_student_welcome_email(inactive_existing.email, inactive_existing.name)

        return inactive_existing

    s = Student(
        college=faculty_college,
        name=name,
        usn=usn,
        branch=branch,
        email=email,
        student_type=stype,
        required_total_points=required_points,
        total_points_earned=0,
        passout_year=payload.passout_year,
        admitted_year=payload.admitted_year,
        created_by_faculty_id=faculty_id,
        is_active=True,
    )

    db.add(s)
    await db.commit()
    await db.refresh(s)

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

    Admin upload CSV:
      name,email,usn,branch,college,student_type,admitted_year,passout_year,faculty_email

    Permanent duplicate behavior:
      - Active existing student => skip / duplicate
      - Inactive existing student => reactivate and update details
      - No existing student => insert new row
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
        return (
            0,
            0,
            0,
            0,
            [
                "CSV has no headers. Required: "
                "name,email,usn,branch,student_type,admitted_year,passout_year"
            ],
        )

    field_map, headers = _normalize_csv_headers(reader.fieldnames)

    required = {"name", "email", "usn", "branch", "passout_year", "admitted_year"}

    if allow_csv_college:
        required.add("college")

    missing = required - headers

    if missing:
        return (0, 0, 0, 0, [f"Missing headers: {', '.join(sorted(missing))}"])

    rows = list(reader)
    total_rows = len(rows)

    # ✅ Permanent fix: preload full Student objects, not only email/usn.
    # We must know active vs inactive.
    existing_students = (await db.execute(select(Student))).scalars().all()

    active_usns: set[tuple[str, str]] = set()
    active_emails: set[tuple[str, str]] = set()

    inactive_by_usn: dict[tuple[str, str], Student] = {}
    inactive_by_email: dict[tuple[str, str], Student] = {}

    for s in existing_students:
        college_key = str(getattr(s, "college", "") or "").strip().lower()
        usn_key = str(getattr(s, "usn", "") or "").strip().upper()
        email_key = str(getattr(s, "email", "") or "").strip().lower()

        if not college_key:
            continue

        if _is_active_student(s):
            if usn_key:
                active_usns.add((college_key, usn_key))
            if email_key:
                active_emails.add((college_key, email_key))
        else:
            if usn_key:
                inactive_by_usn.setdefault((college_key, usn_key), s)
            if email_key:
                inactive_by_email.setdefault((college_key, email_key), s)

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

                    if not row_college:
                        row_college = faculty.college

            if not name or not email or not usn or not branch:
                raise ValueError("name/email/usn/branch cannot be empty")

            college_key = row_college.strip().lower()
            usn_key = usn.strip().upper()
            email_key = email.strip().lower()

            active_dup = (
                (college_key, usn_key) in active_usns
                or (college_key, email_key) in active_emails
            )

            if active_dup:
                if skip_duplicates:
                    skipped += 1
                    continue

                raise ValueError("Duplicate USN/email in this college")

            stype = _parse_student_type(stype_raw)

            # ✅ Permanent fix:
            # If same USN/email exists but inactive, restore that student instead of skipping.
            inactive_student = (
                inactive_by_usn.get((college_key, usn_key))
                or inactive_by_email.get((college_key, email_key))
            )

            if inactive_student:
                _reactivate_student(
                    inactive_student,
                    college=row_college,
                    name=name,
                    email=email,
                    usn=usn,
                    branch=branch,
                    student_type=stype,
                    passout_year=passout_year,
                    admitted_year=admitted_year,
                    faculty_id=row_faculty_id,
                )

                inserted += 1

                active_usns.add((college_key, usn_key))
                active_emails.add((college_key, email_key))

                inactive_by_usn.pop((college_key, usn_key), None)
                inactive_by_email.pop((college_key, email_key), None)

                welcome_targets.append((email, name))
                continue

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
                is_active=True,
            )

            db.add(s)
            inserted += 1

            active_usns.add((college_key, usn_key))
            active_emails.add((college_key, email_key))

            welcome_targets.append((email, name))

        except Exception as e:
            invalid += 1
            errors.append(f"Row {idx}: {str(e)}")

    await db.commit()

    for email, name in welcome_targets:
        _trigger_student_welcome_email(email, name)

    return (total_rows, inserted, skipped, invalid, errors)