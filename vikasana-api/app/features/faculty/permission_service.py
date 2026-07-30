from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
    apply_student_scope_to_statement,
)
from app.features.students.models import Student


async def get_student_within_website_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    student_id: int,
) -> Student:
    """
    Return a student only when the student belongs to the supplied
    website Faculty authorization scope.

    The scoped query prevents users from discovering whether a
    student exists outside their permitted college, department, or
    assigned-student scope.

    This helper performs one read query. It does not mutate, commit,
    refresh, or delete any database record.
    """
    if (
        not isinstance(student_id, int)
        or isinstance(student_id, bool)
        or student_id <= 0
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="student_id must be a positive integer",
        )

    statement = select(Student).where(
        Student.id == student_id
    )

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    result = await db.execute(statement)
    student = result.scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found",
        )

    return student

async def list_students_within_website_scope(
    *,
    db: AsyncSession,
    scope: WebsiteFacultyScope,
    offset: int = 0,
    limit: int = 100,
) -> list[Student]:
    """
    Return students visible within the supplied website Faculty scope.

    Results are ordered by Student.id for stable pagination.

    This helper performs one read query. It does not mutate, commit,
    refresh, delete, or otherwise modify database records.
    """
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="offset must be a non-negative integer",
        )

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > 500
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 500",
        )

    statement = select(Student)

    statement = apply_student_scope_to_statement(
        statement,
        scope,
    )

    statement = (
        statement
        .order_by(Student.id.asc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(statement)

    return list(result.scalars().all())
