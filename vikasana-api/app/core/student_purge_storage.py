from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from minio.deleteobjects import DeleteObject

from app.core.minio_client import get_minio


# ============================================================
# STORAGE REFERENCE
# ============================================================


@dataclass(frozen=True, slots=True)
class PurgeStorageObject:
    bucket: str
    object_key: str


# ============================================================
# PROVIDER / BUCKETS
# ============================================================


def _env(
    name: str,
    default: str = "",
) -> str:
    return os.getenv(
        name,
        default,
    ).strip()


def _storage_provider() -> str:
    return _env(
        "S3_PROVIDER",
        "minio",
    ).lower()


def get_purge_activity_bucket() -> str:
    if _storage_provider() == "aws":
        return _env(
            "AWS_S3_BUCKET_ACTIVITIES",
            "activity-uploads",
        )

    return _env(
        "MINIO_BUCKET_ACTIVITIES",
        "activity-uploads",
    )


def get_purge_face_bucket() -> str:
    if _storage_provider() == "aws":
        return _env(
            "AWS_S3_BUCKET_FACE",
            "face-verification",
        )

    return _env(
        "MINIO_FACE_BUCKET",
        "face-verification",
    )


# ============================================================
# FAIL-CLOSED KEY NORMALIZATION
# ============================================================


def _known_bucket_names() -> set[str]:
    return {
        value
        for value in {
            get_purge_activity_bucket(),
            get_purge_face_bucket(),
            _env(
                "MINIO_BUCKET_ACTIVITIES",
                "activity-uploads",
            ),
            _env(
                "MINIO_FACE_BUCKET",
                "face-verification",
            ),
            _env(
                "AWS_S3_BUCKET_ACTIVITIES",
                "activity-uploads",
            ),
            _env(
                "AWS_S3_BUCKET_FACE",
                "face-verification",
            ),
        }
        if value
    }


def normalize_purge_object_key(
    value: str,
) -> str:
    """
    Convert a stored object key / public URL / presigned URL
    into a plain object key.

    Only known storage bucket prefixes are stripped.

    This function does NOT decide whether the resulting key is
    allowed to be deleted. Callers must apply a strict prefix or
    pattern check afterwards.
    """

    raw = str(
        value or ""
    ).strip()

    if not raw:
        raise ValueError(
            "Storage object value is empty"
        )

    raw = raw.replace(
        "\\",
        "/",
    )

    raw = raw.split(
        "?",
        1,
    )[0]

    if "://" in raw:
        parsed = urlparse(
            raw
        )

        raw = unquote(
            parsed.path or ""
        )

    raw = raw.lstrip("/")

    # Existing legacy proxy URLs may look like:
    #
    # minio/activity-uploads/activities/...
    if raw.startswith(
        "minio/"
    ):
        raw = raw[
            len("minio/"):
        ]

    changed = True

    while changed:
        changed = False

        for bucket in (
            _known_bucket_names()
        ):
            prefix = (
                f"{bucket}/"
            )

            if raw.startswith(
                prefix
            ):
                raw = raw[
                    len(prefix):
                ]

                changed = True

    raw = raw.lstrip("/")

    if not raw:
        raise ValueError(
            "Storage object key is empty"
        )

    parts = raw.split("/")

    if any(
        part in {
            "",
            ".",
            "..",
        }
        for part in parts
    ):
        raise ValueError(
            "Unsafe storage object key"
        )

    return raw


# ============================================================
# ACTIVITY + EVENT PHOTOS
# ============================================================


def activity_photo_object(
    *,
    image_url: str,
    student_id: int,
) -> PurgeStorageObject:
    """
    ActivityPhoto and EventSubmissionPhoto are both uploaded by
    upload_activity_image(), which stores them under:

        activities/{student_id}/{session_or_submission_id}/...
    """

    student_id = int(
        student_id
    )

    if student_id < 1:
        raise ValueError(
            "student_id must be positive"
        )

    key = (
        normalize_purge_object_key(
            image_url
        )
    )

    expected_prefix = (
        f"activities/{student_id}/"
    )

    if not key.startswith(
        expected_prefix
    ):
        raise ValueError(
            "Activity photo key is outside the student's allowed prefix"
        )

    remainder = key[
        len(expected_prefix):
    ]

    # Must contain:
    #
    # {session_or_submission_id}/{filename}
    if (
        "/" not in remainder
        or not remainder.split(
            "/",
            1,
        )[0].isdigit()
    ):
        raise ValueError(
            "Activity photo key has an unexpected structure"
        )

    return PurgeStorageObject(
        bucket=get_purge_activity_bucket(),
        object_key=key,
    )


# ============================================================
# FACE ENROLLMENT IMAGES
# ============================================================


def face_enrollment_object(
    *,
    image_key: str,
    student_id: int,
) -> PurgeStorageObject:
    student_id = int(
        student_id
    )

    if student_id < 1:
        raise ValueError(
            "student_id must be positive"
        )

    key = (
        normalize_purge_object_key(
            image_key
        )
    )

    expected_prefix = (
        f"face-enrollment/{student_id}/"
    )

    if not key.startswith(
        expected_prefix
    ):
        raise ValueError(
            "Face enrollment key is outside the student's allowed prefix"
        )

    filename = key[
        len(expected_prefix):
    ]

    if (
        not filename
        or "/" in filename
    ):
        raise ValueError(
            "Face enrollment key has an unexpected structure"
        )

    return PurgeStorageObject(
        bucket=get_purge_face_bucket(),
        object_key=key,
    )


# ============================================================
# PROCESSED / BOXED FACE IMAGES
# ============================================================


_PROCESSED_FACE_PATTERN = re.compile(
    r"^(?P<session_id>[1-9][0-9]*)/"
    r"(?P<photo_id>[1-9][0-9]*)"
    r"_boxed_"
    r"(?P<uuid>[0-9a-fA-F]{32})"
    r"\.jpg$"
)


def processed_face_object(
    *,
    processed_object: str | None,
    session_id: int,
    photo_id: int,
) -> PurgeStorageObject | None:
    """
    Current real processed face keys are:

        {session_id}/{photo_id}_boxed_{uuid.hex}.jpg

    Legacy metadata literal:

        activity_photo_upload

    is NOT a storage object and is intentionally ignored.
    """

    value = str(
        processed_object or ""
    ).strip()

    if not value:
        return None

    if value == (
        "activity_photo_upload"
    ):
        return None

    session_id = int(
        session_id
    )

    photo_id = int(
        photo_id
    )

    if (
        session_id < 1
        or photo_id < 1
    ):
        raise ValueError(
            "Invalid processed face identifiers"
        )

    key = (
        normalize_purge_object_key(
            value
        )
    )

    match = (
        _PROCESSED_FACE_PATTERN
        .fullmatch(
            key
        )
    )

    if match is None:
        raise ValueError(
            "Processed face object key has an unknown format"
        )

    if (
        int(
            match.group(
                "session_id"
            )
        )
        != session_id
    ):
        raise ValueError(
            "Processed face session does not match"
        )

    if (
        int(
            match.group(
                "photo_id"
            )
        )
        != photo_id
    ):
        raise ValueError(
            "Processed face photo does not match"
        )

    return PurgeStorageObject(
        bucket=get_purge_face_bucket(),
        object_key=key,
    )


# ============================================================
# DEDUPLICATION
# ============================================================


def deduplicate_purge_objects(
    objects: list[
        PurgeStorageObject
    ],
) -> list[
    PurgeStorageObject
]:
    unique = {
        (
            item.bucket,
            item.object_key,
        ): item
        for item in objects
    }

    return [
        unique[key]
        for key in sorted(
            unique
        )
    ]


# ============================================================
# PHYSICAL STORAGE DELETE
# ============================================================


def delete_purge_storage_objects(
    objects: list[
        PurgeStorageObject
    ],
    *,
    chunk_size: int = 1000,
) -> int:
    """
    Physically remove validated storage objects.

    Properties:
    - provider-neutral through the existing MinIO-compatible
      client, including AWS S3 mode;
    - grouped by bucket;
    - deduplicated;
    - chunked;
    - S3/MinIO delete semantics are idempotent for an object
      which is already absent;
    - any reported delete error aborts the purge workflow.

    The caller must perform this BEFORE committing the
    ARCHIVED -> PURGED database mutation.
    """

    chunk_size = max(
        1,
        min(
            int(
                chunk_size
            ),
            1000,
        ),
    )

    items = (
        deduplicate_purge_objects(
            objects
        )
    )

    if not items:
        return 0

    by_bucket: dict[
        str,
        list[str],
    ] = {}

    for item in items:
        if not item.bucket:
            raise ValueError(
                "Storage bucket is empty"
            )

        if not item.object_key:
            raise ValueError(
                "Storage object key is empty"
            )

        by_bucket.setdefault(
            item.bucket,
            [],
        ).append(
            item.object_key
        )

    client = get_minio()

    deleted = 0

    for bucket in sorted(
        by_bucket
    ):
        keys = sorted(
            set(
                by_bucket[
                    bucket
                ]
            )
        )

        for offset in range(
            0,
            len(keys),
            chunk_size,
        ):
            chunk = keys[
                offset:
                offset + chunk_size
            ]

            errors = list(
                client.remove_objects(
                    bucket_name=bucket,
                    delete_object_list=[
                        DeleteObject(
                            key
                        )
                        for key in chunk
                    ],
                )
            )

            if errors:
                first = errors[0]

                code = str(
                    getattr(
                        first,
                        "code",
                        "UNKNOWN",
                    )
                )

                raise RuntimeError(
                    "Storage purge failed "
                    f"for bucket={bucket!r} "
                    f"with code={code!r}"
                )

            deleted += len(
                chunk
            )

    return deleted
