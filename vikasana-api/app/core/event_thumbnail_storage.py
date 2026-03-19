import io
import os
import uuid

from fastapi import HTTPException, UploadFile

from app.core.minio_client import get_minio, ensure_bucket


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


async def upload_event_thumbnail_file(
    file: UploadFile,
    admin_id: int,
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    if file.content_type not in ALLOWED_IMAGE_TYPES:
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

    bucket = os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS", "vikasana-event-thumbnails")
    ensure_bucket(minio, bucket)

    ext = file.filename.split(".")[-1].lower() if "." in file.filename else "jpg"
    object_name = f"thumbnails/{admin_id}/{uuid.uuid4().hex}.{ext}"

    minio.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(data),
        length=len(data),
        content_type=file.content_type,
    )

    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    public_url = f"{public_base}/{bucket}/{object_name}" if public_base else object_name

    return {
        "object_name": object_name,
        "public_url": public_url,
        "content_type": file.content_type,
        "size": len(data),
    }