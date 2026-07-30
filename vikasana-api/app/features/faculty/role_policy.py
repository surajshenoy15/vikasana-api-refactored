"""
Canonical Faculty management roles.

Authentication remains based on the generic Faculty account type.
These values represent the authorization role stored in Faculty.role.
"""

ROLE_FACULTY = "faculty"
ROLE_COLLEGE_COORDINATOR = "college_coordinator"
ROLE_HOD = "hod"
ROLE_FACULTY_COORDINATOR = "faculty_coordinator"


ALL_FACULTY_ROLES: frozenset[str] = frozenset(
    {
        ROLE_FACULTY,
        ROLE_COLLEGE_COORDINATOR,
        ROLE_HOD,
        ROLE_FACULTY_COORDINATOR,
    }
)


WEBSITE_MANAGEMENT_ROLES: frozenset[str] = frozenset(
    {
        ROLE_COLLEGE_COORDINATOR,
        ROLE_HOD,
        ROLE_FACULTY_COORDINATOR,
    }
)


_ROLE_ALIASES: dict[str, str] = {
    "faculty": ROLE_FACULTY,
    "college_coordinator": ROLE_COLLEGE_COORDINATOR,
    "hod": ROLE_HOD,
    "head_of_department": ROLE_HOD,
    "faculty_coordinator": ROLE_FACULTY_COORDINATOR,
}


def _normalize_role_key(value: str) -> str:
    normalized = value.strip().casefold()

    normalized = normalized.replace("-", "_")
    normalized = normalized.replace(" ", "_")

    while "__" in normalized:
        normalized = normalized.replace("__", "_")

    return normalized.strip("_")


def normalize_faculty_role(value: str) -> str:
    """
    Normalize a Faculty.role value into a canonical role.

    Unknown and empty values are rejected. This function performs no
    database operation and does not modify the supplied Faculty object.
    """
    if not isinstance(value, str):
        raise ValueError("Faculty role must be a string")

    role_key = _normalize_role_key(value)

    if not role_key:
        raise ValueError("Faculty role is required")

    canonical_role = _ROLE_ALIASES.get(role_key)

    if canonical_role is None:
        allowed_roles = ", ".join(
            sorted(ALL_FACULTY_ROLES)
        )

        raise ValueError(
            "Unsupported faculty role. "
            f"Allowed roles: {allowed_roles}"
        )

    return canonical_role


def is_website_management_role(value: object) -> bool:
    """
    Return whether the supplied value represents a website role.

    Invalid or unknown role values safely return False.
    """
    if not isinstance(value, str):
        return False

    try:
        normalized_role = normalize_faculty_role(value)
    except ValueError:
        return False

    return normalized_role in WEBSITE_MANAGEMENT_ROLES
