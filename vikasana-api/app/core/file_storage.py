import os
import uuid
from io import BytesIO

from app.core.minio_client import get_minio, ensure_bucket, get_presigned_url


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _faculty_bucket() -> str:
    """
    Uses MinIO bucket in current Hostinger setup.
    Uses AWS bucket when S3_PROVIDER=aws later.
    """
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_FACULTY", "vikasana-faculty")

    return _env("MINIO_BUCKET_FACULTY", "vikasana-faculty")


async def upload_faculty_image(
    file_bytes: bytes,
    content_type: str,
    filename: str,
) -> str:
    if not file_bytes:
        raise ValueError("file_bytes is required")

    minio = get_minio()
    bucket = _faculty_bucket()
    ensure_bucket(minio, bucket)

    ext = filename.split(".")[-1].lower() if filename and "." in filename else "jpg"
    object_name = f"faculty/{uuid.uuid4().hex}.{ext}"

    data = BytesIO(file_bytes)

    minio.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=data,
        length=len(file_bytes),
        content_type=content_type or "application/octet-stream",
    )

    # For existing MinIO public URL setup, keep direct public URL behavior
    # so your current frontend does not break.
    if _storage_provider() == "minio":
        public_base = _env("MINIO_PUBLIC_BASE").rstrip("/")
        if public_base:
            return f"{public_base}/{bucket}/{object_name}"

    # For AWS S3 or fallback, return presigned URL.
    return get_presigned_url(
        bucket=bucket,
        object_name=object_name,
        expiry_seconds=3600,
        public=True,
    )