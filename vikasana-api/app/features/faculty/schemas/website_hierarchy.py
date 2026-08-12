from pydantic import BaseModel, EmailStr, Field, field_validator

from app.features.organization.schemas import (
    DepartmentCodeStr,
    DepartmentNameStr,
)


class WebsiteDepartmentCreateRequest(BaseModel):
    """
    Request used by a College Coordinator to create
    a department.

    College and creator provenance are derived from
    the authenticated College Coordinator and are
    never accepted from the client.
    """

    name: DepartmentNameStr
    code: DepartmentCodeStr

    @field_validator("code")
    @classmethod
    def normalize_code(
        cls,
        value: str,
    ) -> str:
        return value.upper()


class WebsiteHODCreateRequest(BaseModel):
    """
    Request used by a College Coordinator to create an HOD.

    College, role, parent Faculty and creator are derived from the
    authenticated College Coordinator and are never accepted from
    the client.
    """

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
    )

    email: EmailStr

    department_id: int = Field(
        ...,
        ge=1,
    )

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "full_name cannot be empty"
            )

        return normalized


class WebsiteFacultyMentorCreateRequest(BaseModel):
    """
    Request used by an HOD to create a Faculty/Mentor account.

    College, department, role, parent Faculty and creator are
    derived from the authenticated HOD and are never accepted from
    the client.
    """

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
    )

    email: EmailStr

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "full_name cannot be empty"
            )

        return normalized
