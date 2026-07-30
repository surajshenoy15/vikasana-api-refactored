from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_FACULTY_COORDINATOR,
    ROLE_HOD,
    normalize_faculty_role,
)


class FacultyScopeType(str, Enum):
    COLLEGE = "college"
    DEPARTMENT = "department"


class FacultyLike(Protocol):
    id: int
    college: str
    role: str
    department_id: int | None


class StudentLike(Protocol):
    college: str
    department_id: int | None


@dataclass(frozen=True, slots=True)
class WebsiteFacultyScope:
    faculty_id: int
    role: str
    scope_type: FacultyScopeType
    college: str
    department_id: int | None = None


def normalize_scope_college(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Faculty college is required")

    normalized = value.strip()

    if not normalized:
        raise ValueError("Faculty college is required")

    return normalized


def _college_key(value: object) -> str:
    if not isinstance(value, str):
        return ""

    return value.strip().casefold()


def resolve_website_faculty_scope(
    faculty: FacultyLike,
) -> WebsiteFacultyScope:
    """
    Resolve a website Faculty account into an immutable authorization
    scope without mutating the supplied Faculty ORM object.

    College Coordinator receives college-wide access.

    HOD and Faculty Coordinator receive access only to their own
    department.
    """
    faculty_id = getattr(faculty, "id", None)

    if (
        not isinstance(faculty_id, int)
        or isinstance(faculty_id, bool)
        or faculty_id <= 0
    ):
        raise ValueError(
            "Faculty ID must be a positive integer"
        )

    role = normalize_faculty_role(
        getattr(faculty, "role", "")
    )

    college = normalize_scope_college(
        getattr(faculty, "college", None)
    )

    department_id = getattr(
        faculty,
        "department_id",
        None,
    )

    if role == ROLE_COLLEGE_COORDINATOR:
        return WebsiteFacultyScope(
            faculty_id=faculty_id,
            role=role,
            scope_type=FacultyScopeType.COLLEGE,
            college=college,
            department_id=None,
        )

    if role in {
        ROLE_HOD,
        ROLE_FACULTY_COORDINATOR,
    }:
        if (
            not isinstance(department_id, int)
            or isinstance(department_id, bool)
            or department_id <= 0
        ):
            raise ValueError(
                f"{role} requires a valid department_id"
            )

        return WebsiteFacultyScope(
            faculty_id=faculty_id,
            role=role,
            scope_type=FacultyScopeType.DEPARTMENT,
            college=college,
            department_id=department_id,
        )

    raise ValueError(
        "Faculty role does not have website management scope"
    )


def student_is_within_faculty_scope(
    scope: WebsiteFacultyScope,
    student: StudentLike,
) -> bool:
    """
    Check whether a student belongs to the website Faculty scope.

    College Coordinator:
        same college

    HOD and Faculty Coordinator:
        same college and department
    """
    if _college_key(
        getattr(student, "college", None)
    ) != _college_key(scope.college):
        return False

    if scope.scope_type == FacultyScopeType.COLLEGE:
        return True

    if scope.scope_type == FacultyScopeType.DEPARTMENT:
        return (
            getattr(student, "department_id", None)
            == scope.department_id
        )

    return False


def apply_student_scope_to_statement(
    statement: Any,
    scope: WebsiteFacultyScope,
) -> Any:
    """
    Add website Faculty authorization filters to a Student query.

    The supplied statement is not mutated and no database query is
    executed by this helper.
    """
    from sqlalchemy import func

    from app.features.students.models import Student

    college_key = normalize_scope_college(
        scope.college
    ).casefold()

    scoped_statement = statement.where(
        func.lower(
            func.trim(Student.college)
        )
        == college_key
    )

    if scope.scope_type == FacultyScopeType.COLLEGE:
        return scoped_statement

    if scope.scope_type != FacultyScopeType.DEPARTMENT:
        raise ValueError(
            "Unsupported Faculty scope type"
        )

    department_id = scope.department_id

    if (
        not isinstance(department_id, int)
        or isinstance(department_id, bool)
        or department_id <= 0
    ):
        raise ValueError(
            "Department-scoped access requires "
            "a valid department_id"
        )

    return scoped_statement.where(
        Student.department_id == department_id
    )
