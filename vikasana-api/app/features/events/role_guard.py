from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.events.models import Event, EventRoleAssignment, EventSubmission


BLOCKING_STATUSES = [
    "DRAFT",
    "SUBMITTED",
    "APPROVED",
    "in_progress",
    "submitted",
    "approved",
]


async def validate_event_role_access(
    *,
    db: AsyncSession,
    student_id: int,
    event_id: int,
) -> Event:
    """
    Blocks illegal double claiming:
    - Volunteer cannot submit participant event.
    - Normal participant cannot submit volunteer event.
    - Same student cannot submit both participant + volunteer event under same exclusive_group_key.
    """

    event = await db.get(Event, event_id)

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    group_key = getattr(event, "exclusive_group_key", None)
    event_role = (getattr(event, "event_role", None) or "PARTICIPANT").upper()

    # Old events without group key continue normally.
    if not group_key:
        return event

    # Check pre-assigned role.
    assignment_result = await db.execute(
        select(EventRoleAssignment).where(
            EventRoleAssignment.student_id == student_id,
            EventRoleAssignment.exclusive_group_key == group_key,
        )
    )
    assignment = assignment_result.scalar_one_or_none()

    if assignment:
        allowed_role = (assignment.role_allowed or "").upper()

        if allowed_role != event_role:
            if allowed_role == "VOLUNTEER" and event_role == "PARTICIPANT":
                raise HTTPException(
                    status_code=403,
                    detail="You are registered as a volunteer for this event. You cannot submit participant activity for the same event.",
                )

            if allowed_role == "PARTICIPANT" and event_role == "VOLUNTEER":
                raise HTTPException(
                    status_code=403,
                    detail="You are registered as a participant for this event. You cannot claim volunteer points.",
                )

            raise HTTPException(
                status_code=403,
                detail="You are not allowed to submit this event role.",
            )

    else:
        # No assignment = normal participant.
        # So normal students cannot claim volunteer event.
        if event_role == "VOLUNTEER":
            raise HTTPException(
                status_code=403,
                detail="Only assigned volunteers can submit volunteer activity for this event.",
            )

    # Block double claim across same main event group.
    existing_result = await db.execute(
        select(EventSubmission)
        .join(Event, EventSubmission.event_id == Event.id)
        .where(
            EventSubmission.student_id == student_id,
            Event.exclusive_group_key == group_key,
            EventSubmission.event_id != event_id,
            EventSubmission.status.in_(BLOCKING_STATUSES),
        )
    )

    existing_submission = existing_result.scalar_one_or_none()

    if existing_submission:
        raise HTTPException(
            status_code=409,
            detail="You have already submitted another role for this event. You cannot claim both participant and volunteer points.",
        )

    return event


async def can_student_view_event(
    *,
    db: AsyncSession,
    student_id: int,
    event: Event,
) -> bool:
    """
    Used in GET /student/events to hide wrong role events.
    """

    group_key = getattr(event, "exclusive_group_key", None)
    event_role = (getattr(event, "event_role", None) or "PARTICIPANT").upper()

    if not group_key:
        return True

    assignment_result = await db.execute(
        select(EventRoleAssignment).where(
            EventRoleAssignment.student_id == student_id,
            EventRoleAssignment.exclusive_group_key == group_key,
        )
    )
    assignment = assignment_result.scalar_one_or_none()

    if assignment:
        allowed_role = (assignment.role_allowed or "").upper()
        return allowed_role == event_role

    # No assignment = normal participant.
    return event_role == "PARTICIPANT"
