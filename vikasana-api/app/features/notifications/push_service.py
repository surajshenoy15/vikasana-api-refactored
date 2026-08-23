from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


EXPO_PUSH_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_PUSH_RECEIPTS_URL = (
    "https://exp.host/--/api/v2/push/getReceipts"
)
EXPO_PUSH_CHUNK_SIZE = 100
EXPO_RECEIPT_CHUNK_SIZE = 100


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


def _post_expo_receipt_payload(
    receipt_ids: list[str],
) -> dict[str, Any]:
    """
    Fetch Expo push receipts for previously successful
    push ticket IDs.
    """

    request = urllib.request.Request(
        EXPO_PUSH_RECEIPTS_URL,
        data=json.dumps(
            {"ids": receipt_ids}
        ).encode("utf-8"),
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


async def check_expo_push_receipts(
    receipt_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Check Expo push receipts and deactivate only devices
    explicitly reported as DeviceNotRegistered.
    """

    clean_entries: list[dict[str, Any]] = []

    for entry in receipt_entries or []:
        if not isinstance(entry, dict):
            continue

        receipt_id = str(
            entry.get("receipt_id")
            or ""
        ).strip()

        try:
            device_id = int(
                entry.get("device_id")
            )
        except (TypeError, ValueError):
            continue

        if not receipt_id:
            continue

        clean_entries.append(
            {
                "receipt_id": receipt_id,
                "device_id": device_id,
            }
        )

    if not clean_entries:
        return {
            "ok": True,
            "receipts_requested": 0,
            "receipts_found": 0,
            "device_not_registered": 0,
            "devices_deactivated": 0,
            "missing_receipts": 0,
        }

    receipt_to_device = {
        entry["receipt_id"]: entry["device_id"]
        for entry in clean_entries
    }

    receipt_ids = list(
        receipt_to_device.keys()
    )

    receipts_found = 0
    missing_receipts = 0
    receipt_errors = 0
    dead_device_ids: set[int] = set()

    try:
        for index in range(
            0,
            len(receipt_ids),
            EXPO_RECEIPT_CHUNK_SIZE,
        ):
            chunk = receipt_ids[
                index:
                index + EXPO_RECEIPT_CHUNK_SIZE
            ]

            expo_result = await asyncio.to_thread(
                _post_expo_receipt_payload,
                chunk,
            )

            if expo_result.get("http_status") != 200:
                return {
                    "ok": False,
                    "receipts_requested": len(
                        receipt_ids
                    ),
                    "receipts_found": receipts_found,
                    "device_not_registered": len(
                        dead_device_ids
                    ),
                    "devices_deactivated": 0,
                    "missing_receipts": missing_receipts,
                    "error": "expo_receipt_http_error",
                    "http_status": expo_result.get(
                        "http_status"
                    ),
                }

            response_data = expo_result.get(
                "response",
                {},
            )

            receipt_data = (
                response_data.get("data", {})
                if isinstance(response_data, dict)
                else {}
            )

            if not isinstance(
                receipt_data,
                dict,
            ):
                receipt_data = {}

            for receipt_id in chunk:
                receipt = receipt_data.get(
                    receipt_id
                )

                if not isinstance(
                    receipt,
                    dict,
                ):
                    missing_receipts += 1
                    continue

                receipts_found += 1

                if receipt.get("status") != "error":
                    continue

                receipt_errors += 1

                details = receipt.get(
                    "details",
                    {},
                )

                error_code = (
                    details.get("error")
                    if isinstance(details, dict)
                    else None
                )

                if (
                    error_code
                    == "DeviceNotRegistered"
                ):
                    dead_device_ids.add(
                        receipt_to_device[
                            receipt_id
                        ]
                    )

    except Exception as error:
        print(
            "[Push Receipts] Check failed:",
            repr(error),
        )

        return {
            "ok": False,
            "receipts_requested": len(
                receipt_ids
            ),
            "receipts_found": receipts_found,
            "device_not_registered": len(
                dead_device_ids
            ),
            "devices_deactivated": 0,
            "missing_receipts": missing_receipts,
            "error": "expo_receipt_check_failed",
        }

    devices_deactivated = 0

    if dead_device_ids:
        from app.core.database import (
            AsyncSessionLocal,
        )

        async with AsyncSessionLocal() as db:
            for device_id in sorted(
                dead_device_ids
            ):
                result = await db.execute(
                    text(
                        """
                        UPDATE student_push_devices
                        SET
                            is_active = false,
                            updated_at = NOW()
                        WHERE
                            id = :device_id
                            AND is_active = true
                        RETURNING id
                        """
                    ),
                    {
                        "device_id": device_id,
                    },
                )

                if result.scalar_one_or_none():
                    devices_deactivated += 1

            await db.commit()

    summary = {
        "ok": True,
        "receipts_requested": len(
            receipt_ids
        ),
        "receipts_found": receipts_found,
        "receipt_errors": receipt_errors,
        "device_not_registered": len(
            dead_device_ids
        ),
        "devices_deactivated": devices_deactivated,
        "missing_receipts": missing_receipts,
    }

    print(
        "[Push Receipts] Check complete:",
        summary,
    )

    return summary


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
    message_student_ids: list[int] = []
    message_device_ids: list[int] = []

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
        message_student_ids.append(
            int(row["student_id"])
        )
        message_device_ids.append(
            int(row["id"])
        )

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
    successful_student_ids: set[int] = set()
    receipt_entries: list[dict[str, Any]] = []
    ticket_dead_device_ids: set[int] = set()

    try:
        for chunk_start in range(
            0,
            len(messages),
            EXPO_PUSH_CHUNK_SIZE,
        ):
            chunk = messages[
                chunk_start:
                chunk_start + EXPO_PUSH_CHUNK_SIZE
            ]

            chunk_student_ids = message_student_ids[
                chunk_start:
                chunk_start + EXPO_PUSH_CHUNK_SIZE
            ]

            chunk_device_ids = message_device_ids[
                chunk_start:
                chunk_start + EXPO_PUSH_CHUNK_SIZE
            ]

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

            for index, ticket in enumerate(
                ticket_data
            ):
                if not isinstance(ticket, dict):
                    continue

                if ticket.get("status") == "ok":
                    tickets_ok += 1

                    if index < len(
                        chunk_student_ids
                    ):
                        successful_student_ids.add(
                            chunk_student_ids[index]
                        )

                    receipt_id = str(
                        ticket.get("id")
                        or ""
                    ).strip()

                    if (
                        receipt_id
                        and index < len(
                            chunk_device_ids
                        )
                    ):
                        receipt_entries.append(
                            {
                                "receipt_id": receipt_id,
                                "device_id": (
                                    chunk_device_ids[index]
                                ),
                            }
                        )
                else:
                    tickets_error += 1

                    details = ticket.get(
                        "details",
                        {},
                    )

                    error_code = (
                        details.get("error")
                        if isinstance(details, dict)
                        else None
                    )

                    if (
                        error_code
                        == "DeviceNotRegistered"
                        and index < len(
                            chunk_device_ids
                        )
                    ):
                        ticket_dead_device_ids.add(
                            chunk_device_ids[index]
                        )

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

    if ticket_dead_device_ids:
        try:
            from app.core.database import (
                AsyncSessionLocal,
            )

            deactivated = 0

            async with AsyncSessionLocal() as cleanup_db:
                for device_id in sorted(
                    ticket_dead_device_ids
                ):
                    result = await cleanup_db.execute(
                        text(
                            """
                            UPDATE student_push_devices
                            SET
                                is_active = false,
                                updated_at = NOW()
                            WHERE
                                id = :device_id
                                AND is_active = true
                            RETURNING id
                            """
                        ),
                        {
                            "device_id": device_id,
                        },
                    )

                    if result.scalar_one_or_none():
                        deactivated += 1

                await cleanup_db.commit()

            print(
                "[Push] DeviceNotRegistered cleanup:",
                {
                    "reported": len(
                        ticket_dead_device_ids
                    ),
                    "deactivated": deactivated,
                },
            )

        except Exception as error:
            print(
                "[Push] Failed ticket cleanup:",
                repr(error),
            )

    if receipt_entries:
        try:
            from app.workers.tasks import (
                check_expo_push_receipts_task,
            )

            check_expo_push_receipts_task.apply_async(
                kwargs={
                    "receipt_entries": (
                        receipt_entries
                    ),
                },
                countdown=900,
            )

            print(
                "[Push] Receipt check queued:",
                {
                    "receipts": len(
                        receipt_entries
                    ),
                    "delay_seconds": 900,
                },
            )

        except Exception as error:
            print(
                "[Push] Failed to queue receipt check:",
                repr(error),
            )

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
        "successful_student_ids": sorted(
            successful_student_ids
        ),
    }
