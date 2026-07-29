from typing import ClassVar

from pydantic import BaseModel, Field, model_validator


class StudentAssignmentUpdateRequest(BaseModel):
    """
    Academic and faculty assignment values for one student.

    Fields are optional so callers can perform partial updates.
    Explicit null values are allowed and represent clearing an
    existing assignment.
    """

    assignment_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "department_id",
            "batch_id",
            "current_year",
            "assigned_faculty_id",
        }
    )

    department_id: int | None = Field(
        default=None,
        ge=1,
    )
    batch_id: int | None = Field(
        default=None,
        ge=1,
    )
    current_year: int | None = Field(
        default=None,
        ge=1,
        le=8,
    )
    assigned_faculty_id: int | None = Field(
        default=None,
        ge=1,
    )

    @model_validator(mode="after")
    def require_assignment_change(
        self,
    ) -> "StudentAssignmentUpdateRequest":
        supplied_assignment_fields = (
            self.model_fields_set
            & self.assignment_fields
        )

        if not supplied_assignment_fields:
            raise ValueError(
                "At least one assignment field must be provided"
            )

        return self


class StudentBulkAssignmentRequest(
    StudentAssignmentUpdateRequest
):
    """
    Apply the same assignment update to multiple students.

    The service and route will be implemented separately in the
    following Stage 6 steps.
    """

    student_ids: list[int] = Field(
        min_length=1,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_student_ids(
        self,
    ) -> "StudentBulkAssignmentRequest":
        if len(self.student_ids) != len(set(self.student_ids)):
            raise ValueError(
                "student_ids must not contain duplicates"
            )

        return self


class StudentBulkAssignmentResponse(BaseModel):
    """Summary returned after a successful bulk assignment."""

    updated_count: int = Field(ge=0)
    student_ids: list[int]
