import os
import uuid
from datetime import timedelta

from fastapi import HTTPException

from app.core.minio_client import get_minio, ensure_bucket


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def generate_event_thumbnail_presigned_put(
    filename: str,
    content_type: str,
    admin_id: int,
):
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content_type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    minio = get_minio()

    bucket = os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS", "vikasana-event-thumbnails")
    ensure_bucket(minio, bucket)

    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    object_name = f"thumbnails/{admin_id}/{uuid.uuid4().hex}.{ext}"

    upload_url = minio.presigned_put_object(
        bucket,
        object_name,
        expires=timedelta(minutes=15),
    )

    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    if public_base:
        public_url = f"{public_base}/{bucket}/{object_name}"
    else:
        public_url = minio.presigned_get_object(bucket, object_name)

    return {"upload_url": upload_url, "public_url": public_url}