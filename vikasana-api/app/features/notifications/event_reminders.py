from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal

# Register SQLAlchemy models required by Student relationships.
# Celery does not load the full FastAPI route import graph, so these
# imports ensure string-based relationship targets are available.
import app.features.auth.models
import app.features.organization.models
import app.features.faculty.models
import app.features.activities.models
import app.features.face.models
from app.features.events.models import Event
from app.features.events.role_guard import can_student_view_event
from app.features.notifications.push_service import (
    send_student_push_notifications,
)
from app.features.students.models import (
    Student,
    StudentPushDevice,
)


IST = ZoneInfo("Asia/Kolkata")

REMINDER_WINDOWS = (
    {
        "key": "24h",
        "type": "event_reminder_24h",
        "target": timedelta(hours=24),
        "tolerance": timedelta(minutes=5),
    },
    {
        "key": "2h",
        "type": "event_reminder_2h",
        "target": timedelta(hours=2),
        "tolerance": timedelta(minutes=5),
    },
)


def _event_start_datetime(
    event: Event,
) -> datetime | None:
    if (
        event.event_date is None
        or event.start_time is None
    ):
        return None

    return datetime.combine(
        event.event_date,
        event.start_time,
        tzinfo=IST,
    )


def _is_due(
    *,
    event_start: datetime,
    now: datetime,
    target: timedelta,
    tolerance: timedelta,
) -> bool:
    remaining = event_start - now

    return (
        target - tolerance
        <= remaining
        <= target + tolerance
    )


async def _get_visible_registered_students(
    db,
    *,
    event: Event,
) -> list[int]:
    result = await db.execute(
        select(Student.id)
        .join(
            StudentPushDevice,
            StudentPushDevice.student_id
            == Student.id,
        )
        .where(
            Student.is_active.is_(True),
            StudentPushDevice.is_active.is_(True),
        )
        .distinct()
    )

    candidate_student_ids = list(
        result.scalars().all()
    )

    visible_student_ids: list[int] = []

    for student_id in candidate_student_ids:
        if await can_student_view_event(
            db=db,
            student_id=student_id,
            event=event,
        ):
            visible_student_ids.append(
                student_id
            )

    return visible_student_ids


async def _remove_already_notified_students(
    db,
    *,
    event_id: int,
    notification_type: str,
    student_ids: list[int],
) -> list[int]:
    if not student_ids:
        return []

    dedupe_by_student = {
        student_id: (
            f"event:{event_id}:"
            f"student:{student_id}:"
            f"{notification_type}"
        )
        for student_id in student_ids
    }

    dedupe_keys = list(
        dedupe_by_student.values()
    )

    result = await db.execute(
        text(
            """
            SELECT dedupe_key
            FROM student_notification_deliveries
            WHERE dedupe_key = ANY(:dedupe_keys)
            """
        ),
        {
            "dedupe_keys": dedupe_keys,
        },
    )

    existing_keys = {
        str(row[0])
        for row in result.all()
    }

    return [
        student_id
        for student_id, dedupe_key
        in dedupe_by_student.items()
        if dedupe_key not in existing_keys
    ]


async def _record_successful_deliveries(
    db,
    *,
    event_id: int,
    notification_type: str,
    student_ids: list[int],
    scheduled_for: datetime,
) -> None:
    if not student_ids:
        return

    for student_id in student_ids:
        dedupe_key = (
            f"event:{event_id}:"
            f"student:{student_id}:"
            f"{notification_type}"
        )

        await db.execute(
            text(
                """
                INSERT INTO student_notification_deliveries (
                    student_id,
                    event_id,
                    notification_type,
                    dedupe_key,
                    status,
                    scheduled_for,
                    sent_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    :student_id,
                    :event_id,
                    :notification_type,
                    :dedupe_key,
                    'SENT',
                    :scheduled_for,
                    NOW(),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (dedupe_key)
                DO NOTHING
                """
            ),
            {
                "student_id": student_id,
                "event_id": event_id,
                "notification_type": (
                    notification_type
                ),
                "dedupe_key": dedupe_key,
                "scheduled_for": scheduled_for,
            },
        )

    await db.commit()


async def run_event_reminders() -> dict:
    now = datetime.now(IST)

    summary = {
        "checked_at": now.isoformat(),
        "events_checked": 0,
        "reminders_due": 0,
        "messages_sent": 0,
        "errors": [],
    }

    async with AsyncSessionLocal() as db:
        max_date = (
            now + timedelta(hours=25)
        ).date()

        result = await db.execute(
            select(Event)
            .where(
                Event.is_active.is_(True),
                Event.event_date.is_not(None),
                Event.start_time.is_not(None),
                Event.event_date >= now.date(),
                Event.event_date <= max_date,
            )
            .order_by(
                Event.event_date.asc(),
                Event.start_time.asc(),
            )
        )

        events = list(
            result.scalars().all()
        )

        summary["events_checked"] = len(events)

        for event in events:
            event_start = (
                _event_start_datetime(event)
            )

            if event_start is None:
                continue

            if event_start <= now:
                continue

            for reminder in REMINDER_WINDOWS:
                if not _is_due(
                    event_start=event_start,
                    now=now,
                    target=reminder["target"],
                    tolerance=reminder[
                        "tolerance"
                    ],
                ):
                    continue

                summary["reminders_due"] += 1

                try:
                    visible_student_ids = (
                        await
                        _get_visible_registered_students(
                            db,
                            event=event,
                        )
                    )

                    target_student_ids = (
                        await
                        _remove_already_notified_students(
                            db,
                            event_id=event.id,
                            notification_type=reminder[
                                "type"
                            ],
                            student_ids=(
                                visible_student_ids
                            ),
                        )
                    )

                    if not target_student_ids:
                        continue

                    time_label = (
                        event_start.strftime(
                            "%I:%M %p"
                        ).lstrip("0")
                    )

                    if reminder["key"] == "24h":
                        body = (
                            f"{event.title} starts "
                            f"tomorrow at {time_label}."
                        )
                    else:
                        body = (
                            f"{event.title} starts "
                            f"in about 2 hours at "
                            f"{time_label}."
                        )

                    if event.venue_name:
                        body += (
                            f" Venue: "
                            f"{event.venue_name}."
                        )

                    push_result = (
                        await
                        send_student_push_notifications(
                            db,
                            title="Event Reminder ⏰",
                            body=body,
                            data={
                                "type": (
                                    "event_reminder"
                                ),
                                "reminder": reminder[
                                    "key"
                                ],
                                "event_id": event.id,
                                "event_title": (
                                    event.title
                                ),
                                "route": (
                                    "/(student)/"
                                    "dashboard"
                                ),
                            },
                            student_ids=(
                                target_student_ids
                            ),
                        )
                    )

                    messages_sent = int(
                        push_result.get(
                            "messages_sent",
                            0,
                        )
                        or 0
                    )

                    successful_student_ids = [
                        int(student_id)
                        for student_id in (
                            push_result.get(
                                "successful_student_ids",
                                [],
                            )
                            or []
                        )
                    ]

                    if successful_student_ids:
                        await (
                            _record_successful_deliveries(
                                db,
                                event_id=event.id,
                                notification_type=(
                                    reminder["type"]
                                ),
                                student_ids=(
                                    successful_student_ids
                                ),
                                scheduled_for=(
                                    event_start
                                    - reminder[
                                        "target"
                                    ]
                                ),
                            )
                        )

                    summary[
                        "messages_sent"
                    ] += len(
                        successful_student_ids
                    )

                    print(
                        "[Push Reminder]",
                        {
                            "event_id": event.id,
                            "reminder": reminder[
                                "key"
                            ],
                            "targets": len(
                                target_student_ids
                            ),
                            "result": push_result,
                        },
                    )

                except Exception as error:
                    await db.rollback()

                    summary["errors"].append(
                        {
                            "event_id": event.id,
                            "reminder": reminder[
                                "key"
                            ],
                            "error": repr(error),
                        }
                    )

                    print(
                        "[Push Reminder] Failed:",
                        {
                            "event_id": event.id,
                            "reminder": reminder[
                                "key"
                            ],
                            "error": repr(error),
                        },
                    )

    return summary
