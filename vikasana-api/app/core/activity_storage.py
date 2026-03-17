import os
import uuid
import logging
from io import BytesIO
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.minio_client import get_minio, ensure_bucket

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def upload_activity_image(
    file_bytes: bytes,
    content_type: str,
    filename: str,
    student_id: int,
    session_id: int,
) -> str:
    """
    Upload activity image to MinIO under:
    activities/{student_id}/{session_id}/{uuid}.ext
    """

    try:
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Empty image file")

        content_type = (content_type or "application/octet-stream").lower().strip()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {content_type}")

        bucket = os.getenv("MINIO_BUCKET_ACTIVITIES", "vikasana-activities").strip()
        public_base = os.getenv("MINIO_PUBLIC_BASE", "").rstrip("/").strip()

        ext_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }
        ext = ext_map.get(content_type, "jpg")

        object_name = f"activities/{student_id}/{session_id}/{uuid.uuid4().hex}.{ext}"

        logger.info(
            "upload_activity_image start bucket=%s object=%s bytes=%s content_type=%s",
            bucket,
            object_name,
            len(file_bytes),
            content_type,
        )

        def _upload():
            try:
                minio = get_minio()
                ensure_bucket(minio, bucket)

                data = BytesIO(file_bytes)
                minio.put_object(
                    bucket_name=bucket,
                    object_name=object_name,
                    data=data,
                    length=len(file_bytes),
                    content_type=content_type,
                )

                if public_base:
                    return f"{public_base}/{bucket}/{object_name}"

                return minio.presigned_get_object(bucket, object_name)

            except Exception as e:
                logger.exception("MinIO upload failed")
                raise RuntimeError(f"MinIO upload failed: {str(e)}") from e

        image_url = await run_in_threadpool(_upload)

        logger.info("upload_activity_image success object=%s", object_name)
        return image_url

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_activity_image crashed")
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")