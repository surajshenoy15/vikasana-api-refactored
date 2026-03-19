import os
import uuid
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException

from app.core.minio_client import get_minio, ensure_bucket


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _rewrite_to_public_base(url: str) -> str:
    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    if not public_base:
        return url

    public_parts = urlsplit(public_base)
    original_parts = urlsplit(url)

    return urlunsplit(
        (
            public_parts.scheme,
            public_parts.netloc,
            original_parts.path,
            original_parts.query,
            original_parts.fragment,
        )
    )


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

    # ✅ rewrite internal Docker URL (http://minio:9000/...)
    #    to public URL (http://31.97.230.171:9000/...)
    upload_url = _rewrite_to_public_base(upload_url)

    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    if public_base:
        public_url = f"{public_base}/{bucket}/{object_name}"
    else:
        public_url = minio.presigned_get_object(bucket, object_name)
        public_url = _rewrite_to_public_base(public_url)

    return {"upload_url": upload_url, "public_url": public_url}