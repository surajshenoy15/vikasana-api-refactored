from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


EXPO_PUSH_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_PUSH_CHUNK_SIZE = 100


def _post_expo_payload(
    payload: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Blocking HTTP request to Expo Push Service.

    Called through asyncio.to_thread() so it does not block
    FastAPI's async event loop.
    """

    request = urllib.request.Request(
        EXPO_PUSH_SEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            body = response.read().decode("utf-8")

            return {
                "http_status": response.status,
                "response": (
                    json.loads(body)
                    if body
                    else {}
                ),
            }

    except urllib.error.HTTPError as error:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        return {
            "http_status": error.code,
            "response": body,
        }


def _chunked(
    items: list[dict[str, Any]],
    size: int,
) -> Iterable[list[dict[str, Any]]]:
    for index in range(
        0,
        len(items),
        size,
    ):
        yield items[index:index + size]


async def send_student_push_notifications(
    db: AsyncSession,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    student_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Send an Expo push notification to active student devices.

    If student_ids is None:
        send to every active registered student device.

    If student_ids is provided:
        send only to devices belonging to those students.

    Push failure is returned as result data instead of raising,
    so notification delivery cannot break event creation.
    """

    params: dict[str, Any] = {}

    sql = """
        SELECT
            id,
            student_id,
            expo_push_token,
            platform
        FROM student_push_devices
        WHERE is_active = true
    """

    if student_ids is not None:
        clean_student_ids = sorted(
            {
                int(student_id)
                for student_id in student_ids
                if student_id is not None
            }
        )

        if not clean_student_ids:
            return {
                "ok": True,
                "registered_devices": 0,
                "messages_sent": 0,
                "tickets_ok": 0,
                "tickets_error": 0,
                "reason": "no_target_students",
            }

        sql += """
            AND student_id = ANY(:student_ids)
        """

        params["student_ids"] = clean_student_ids

    sql += """
        ORDER BY id ASC
    """

    try:
        result = await db.execute(
            text(sql),
            params,
        )

        rows = result.mappings().all()

    except Exception as error:
        print(
            "[Push] Failed to load registered devices:",
            repr(error),
        )

        return {
            "ok": False,
            "registered_devices": 0,
            "messages_sent": 0,
            "tickets_ok": 0,
            "tickets_error": 0,
            "error": "device_lookup_failed",
        }

    messages: list[dict[str, Any]] = []

    for row in rows:
        token = str(
            row["expo_push_token"]
            or ""
        ).strip()

        if not token:
            continue

        if not (
            token.startswith("ExponentPushToken[")
            or token.startswith("ExpoPushToken[")
        ):
            print(
                "[Push] Skipping invalid Expo token for device_id=",
                row["id"],
            )
            continue

        message: dict[str, Any] = {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "priority": "high",
            "data": data or {},
        }

        if row["platform"] == "android":
            message["channelId"] = "events"

        messages.append(message)

    if not messages:
        return {
            "ok": True,
            "registered_devices": len(rows),
            "messages_sent": 0,
            "tickets_ok": 0,
            "tickets_error": 0,
            "reason": "no_valid_push_tokens",
        }

    tickets_ok = 0
    tickets_error = 0
    chunks_sent = 0

    try:
        for chunk in _chunked(
            messages,
            EXPO_PUSH_CHUNK_SIZE,
        ):
            expo_result = await asyncio.to_thread(
                _post_expo_payload,
                chunk,
            )

            chunks_sent += 1

            response_data = expo_result.get(
                "response",
                {},
            )

            ticket_data = (
                response_data.get("data", [])
                if isinstance(response_data, dict)
                else []
            )

            if isinstance(ticket_data, dict):
                ticket_data = [ticket_data]

            for ticket in ticket_data:
                if not isinstance(ticket, dict):
                    continue

                if ticket.get("status") == "ok":
                    tickets_ok += 1
                else:
                    tickets_error += 1

            if expo_result.get("http_status") != 200:
                print(
                    "[Push] Expo HTTP error:",
                    expo_result.get("http_status"),
                )

    except Exception as error:
        print(
            "[Push] Expo send failed:",
            repr(error),
        )

        return {
            "ok": False,
            "registered_devices": len(rows),
            "messages_sent": 0,
            "tickets_ok": tickets_ok,
            "tickets_error": tickets_error,
            "error": "expo_send_failed",
        }

    print(
        "[Push] Send complete:",
        {
            "registered_devices": len(rows),
            "messages_sent": len(messages),
            "tickets_ok": tickets_ok,
            "tickets_error": tickets_error,
            "chunks_sent": chunks_sent,
        },
    )

    return {
        "ok": True,
        "registered_devices": len(rows),
        "messages_sent": len(messages),
        "tickets_ok": tickets_ok,
        "tickets_error": tickets_error,
        "chunks_sent": chunks_sent,
    }
