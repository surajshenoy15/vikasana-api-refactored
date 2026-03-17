import os
import uuid
from typing import Optional
from app.core.minio_client import get_minio, ensure_bucket


async def upload_faculty_image(file_bytes: bytes, content_type: str, filename: str) -> str:
    minio = get_minio()
    bucket = os.getenv("MINIO_BUCKET_FACULTY", "vikasana-faculty")
    ensure_bucket(minio, bucket)

    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    object_name = f"faculty/{uuid.uuid4().hex}.{ext}"

    # MinIO SDK is sync; fine for small images. For huge files, wrap in thread.
    from io import BytesIO
    data = BytesIO(file_bytes)

    minio.put_object(
        bucket,
        object_name,
        data,
        length=len(file_bytes),
        content_type=content_type or "application/octet-stream",
    )

    public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/")
    if public_base:
        return f"{public_base}/{bucket}/{object_name}"

    # fallback presigned
    return minio.presigned_get_object(bucket, object_name)