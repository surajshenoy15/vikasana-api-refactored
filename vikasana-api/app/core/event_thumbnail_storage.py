import io
import os
import uuid

from fastapi import HTTPException, UploadFile

from app.core.minio_client import get_minio, ensure_bucket, get_presigned_url


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _event_thumbnail_bucket() -> str:
    """
    Uses MinIO bucket in current Hostinger setup.
    Uses AWS S3 bucket when S3_PROVIDER=aws later.
    """
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_EVENT_THUMBNAILS", "vikasana-event-thumbnails")

    return _env("MINIO_BUCKET_EVENT_THUMBNAILS", "vikasana-event-thumbnails")


async def upload_event_thumbnail_file(
    file: UploadFile,
    admin_id: int,
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    content_type = (file.content_type or "").lower().strip()

    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content_type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 5 MB")

    minio = get_minio()

    bucket = _event_thumbnail_bucket()
    ensure_bucket(minio, bucket)

    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }

    ext = ext_map.get(content_type, "jpg")
    object_name = f"thumbnails/{admin_id}/{uuid.uuid4().hex}.{ext}"

    minio.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )

    # Keep current Hostinger MinIO public URL behavior
    if _storage_provider() == "minio":
        public_base = _env("MINIO_PUBLIC_BASE").rstrip("/")
        if public_base:
            public_url = f"{public_base}/{bucket}/{object_name}"
        else:
            public_url = get_presigned_url(
                bucket=bucket,
                object_name=object_name,
                expiry_seconds=3600,
                public=True,
            )
    else:
        # AWS S3 mode
        public_url = get_presigned_url(
            bucket=bucket,
            object_name=object_name,
            expiry_seconds=3600,
            public=True,
        )

    return {
        "object_name": object_name,
        "public_url": public_url,
        "content_type": content_type,
        "size": len(data),
        "bucket": bucket,
        "storage_provider": _storage_provider(),
    }