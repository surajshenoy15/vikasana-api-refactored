from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.minio_client import (
    ensure_bucket,
    get_minio,
)


VERIFY_STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class VerifiedBatchExportObject:
    storage_provider: str
    bucket: str
    object_key: str
    sha256: str
    file_size_bytes: int


def get_batch_export_storage_provider() -> str:
    value = os.getenv(
        "S3_PROVIDER",
        "minio",
    ).strip().lower()

    return value or "minio"


def build_batch_export_object_key(
    *,
    export_job_id: int,
    batch_id: int,
    scope_type: str,
    department_id: int | None,
) -> str:
    """
    Stable, non-PII object key.
    """

    if export_job_id < 1:
        raise ValueError("Invalid export_job_id")

    if batch_id < 1:
        raise ValueError("Invalid batch_id")

    scope = str(scope_type or "").strip().lower()

    if scope not in {
        "college",
        "department",
    }:
        raise ValueError("Invalid export scope_type")

    if scope == "department":
        if department_id is None or int(department_id) < 1:
            raise ValueError(
                "department_id required for department export"
            )

        scope_component = (
            f"department-{int(department_id)}"
        )

    else:
        if department_id is not None:
            raise ValueError(
                "department_id must be NULL for college export"
            )

        scope_component = "college"

    return (
        f"batch-exports/"
        f"batch-{int(batch_id)}/"
        f"{scope_component}/"
        f"job-{int(export_job_id)}.zip"
    )


def _sha256_file(
    path: Path,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0

    with path.open("rb") as handle:
        while True:
            block = handle.read(
                VERIFY_STREAM_CHUNK_SIZE
            )

            if not block:
                break

            digest.update(block)
            size += len(block)

    return digest.hexdigest(), size


def _remote_sha256_and_size(
    *,
    client,
    bucket: str,
    object_key: str,
) -> tuple[str, int]:
    """
    Stream the uploaded object back once.

    We do NOT trust ETag as SHA-256 because S3 multipart ETags
    are not content SHA-256 values.
    """

    response = client.get_object(
        bucket,
        object_key,
    )

    digest = hashlib.sha256()
    size = 0

    try:
        while True:
            block = response.read(
                VERIFY_STREAM_CHUNK_SIZE
            )

            if not block:
                break

            digest.update(block)
            size += len(block)

    finally:
        close = getattr(
            response,
            "close",
            None,
        )

        if callable(close):
            close()

        release_conn = getattr(
            response,
            "release_conn",
            None,
        )

        if callable(release_conn):
            release_conn()

    return digest.hexdigest(), size


def upload_and_verify_batch_export(
    *,
    zip_path: str | Path,
    object_key: str,
) -> VerifiedBatchExportObject:
    """
    Upload one final ZIP and verify the exact remote bytes.

    Verification requires:

    1. local SHA-256 + size
    2. successful object upload
    3. stat_object confirms object exists + same size
    4. SHA-256 metadata matches
    5. streamed remote object SHA-256 matches local SHA-256
    6. streamed remote byte count matches local size

    Only after this function succeeds may BatchExportJob.verified_at
    later be populated.
    """

    path = Path(zip_path)

    if not path.is_file():
        raise ValueError(
            "Batch export ZIP does not exist"
        )

    local_sha256, local_size = (
        _sha256_file(path)
    )

    if local_size <= 0:
        raise ValueError(
            "Batch export ZIP is empty"
        )

    bucket = str(
        settings.MINIO_BUCKET_BATCH_EXPORTS
        or ""
    ).strip()

    if not bucket:
        raise RuntimeError(
            "Batch export bucket is not configured"
        )

    client = get_minio()

    ensure_bucket(
        client,
        bucket,
    )

    client.fput_object(
        bucket,
        object_key,
        str(path),
        content_type="application/zip",
        metadata={
            "sha256": local_sha256,
        },
    )

    stat = client.stat_object(
        bucket,
        object_key,
    )

    remote_stat_size = int(
        getattr(stat, "size", -1)
    )

    if remote_stat_size != local_size:
        raise RuntimeError(
            "Uploaded batch export size verification failed"
        )

    metadata = {
        str(key).lower(): str(value)
        for key, value in (
            getattr(stat, "metadata", {})
            or {}
        ).items()
    }

    metadata_sha256 = None

    for key, value in metadata.items():
        if key.endswith(
            "sha256"
        ):
            metadata_sha256 = value
            break

    if (
        metadata_sha256 is not None
        and metadata_sha256
        != local_sha256
    ):
        raise RuntimeError(
            "Uploaded batch export metadata checksum mismatch"
        )

    remote_sha256, remote_size = (
        _remote_sha256_and_size(
            client=client,
            bucket=bucket,
            object_key=object_key,
        )
    )

    if remote_size != local_size:
        raise RuntimeError(
            "Uploaded batch export streamed size mismatch"
        )

    if remote_sha256 != local_sha256:
        raise RuntimeError(
            "Uploaded batch export SHA-256 mismatch"
        )

    return VerifiedBatchExportObject(
        storage_provider=(
            get_batch_export_storage_provider()
        ),
        bucket=bucket,
        object_key=object_key,
        sha256=local_sha256,
        file_size_bytes=local_size,
    )
