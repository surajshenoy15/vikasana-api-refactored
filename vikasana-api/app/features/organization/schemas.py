from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


# --------------------------------------------------
# SHARED FIELD TYPES
# --------------------------------------------------

CollegeStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
    ),
]

DepartmentNameStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=150,
    ),
]

DepartmentCodeStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=30,
    ),
]

BatchNameStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
    ),
]


# --------------------------------------------------
# ORGANISATION SETTINGS
# --------------------------------------------------


class OrganizationSettingsUpdateRequest(BaseModel):
    college: CollegeStr

    department_architecture_enabled: bool = False

    academic_year_start_month: int = Field(
        default=7,
        ge=1,
        le=12,
    )


class OrganizationSettingsResponse(BaseModel):
    id: int | None = None
    college: str

    department_architecture_enabled: bool = False
    academic_year_start_month: int = 7

    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# --------------------------------------------------
# DEPARTMENT SCHEMAS
# --------------------------------------------------


class DepartmentCreateRequest(BaseModel):
    college: CollegeStr
    name: DepartmentNameStr
    code: DepartmentCodeStr

    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_department_code(cls, value: str) -> str:
        return value.upper()


class DepartmentUpdateRequest(BaseModel):
    name: DepartmentNameStr | None = None
    code: DepartmentCodeStr | None = None
    is_active: bool | None = None

    @field_validator("code")
    @classmethod
    def normalize_department_code(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return value.upper()

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> Self:
        if (
            self.name is None
            and self.code is None
            and self.is_active is None
        ):
            raise ValueError(
                "At least one department field must be provided"
            )

        return self


class DepartmentResponse(BaseModel):
    id: int

    college: str
    name: str
    code: str

    is_active: bool

    created_by_faculty_id: int | None = None
    created_by_admin_id: int | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------
# ACADEMIC BATCH SCHEMAS
# --------------------------------------------------


class AcademicBatchCreateRequest(BaseModel):
    college: CollegeStr
    name: BatchNameStr

    admitted_year: int = Field(
        ...,
        ge=1990,
        le=2200,
    )

    passout_year: int = Field(
        ...,
        ge=1991,
        le=2208,
    )

    course_duration_years: int = Field(
        default=4,
        ge=1,
        le=8,
    )

    is_active: bool = True

    @model_validator(mode="after")
    def validate_batch_years(self) -> Self:
        if self.passout_year <= self.admitted_year:
            raise ValueError(
                "passout_year must be greater than admitted_year"
            )

        return self


class AcademicBatchUpdateRequest(BaseModel):
    name: BatchNameStr | None = None

    admitted_year: int | None = Field(
        default=None,
        ge=1990,
        le=2200,
    )

    passout_year: int | None = Field(
        default=None,
        ge=1991,
        le=2208,
    )

    course_duration_years: int | None = Field(
        default=None,
        ge=1,
        le=8,
    )

    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_update_fields(self) -> Self:
        values = (
            self.name,
            self.admitted_year,
            self.passout_year,
            self.course_duration_years,
            self.is_active,
        )

        if all(value is None for value in values):
            raise ValueError(
                "At least one academic batch field must be provided"
            )

        if (
            self.admitted_year is not None
            and self.passout_year is not None
            and self.passout_year <= self.admitted_year
        ):
            raise ValueError(
                "passout_year must be greater than admitted_year"
            )

        return self


class AcademicBatchResponse(BaseModel):
    id: int

    college: str
    name: str

    admitted_year: int
    passout_year: int
    course_duration_years: int

    is_active: bool

    created_by_faculty_id: int | None = None
    created_by_admin_id: int | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}