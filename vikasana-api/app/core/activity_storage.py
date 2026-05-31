import os
import uuid
import logging
from io import BytesIO

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.minio_client import get_minio, ensure_bucket, get_presigned_url

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _activity_bucket() -> str:
    """
    Uses MinIO bucket in current Hostinger setup.
    Uses AWS bucket when S3_PROVIDER=aws later.
    """
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_ACTIVITIES", "activity-uploads")

    return _env("MINIO_BUCKET_ACTIVITIES", "activity-uploads")


async def upload_activity_image(
    file_bytes: bytes,
    content_type: str,
    filename: str,
    student_id: int,
    session_id: int,
) -> str:
    """
    Upload activity image under:
    activities/{student_id}/{session_id}/{uuid}.ext

    Works with:
    - S3_PROVIDER=minio
    - S3_PROVIDER=aws
    """

    try:
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Empty image file")

        content_type = (content_type or "application/octet-stream").lower().strip()

        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image type: {content_type}",
            )

        bucket = _activity_bucket()

        ext_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }

        ext = ext_map.get(content_type, "jpg")
        object_name = f"activities/{student_id}/{session_id}/{uuid.uuid4().hex}.{ext}"

        logger.info(
            "upload_activity_image start provider=%s bucket=%s object=%s bytes=%s content_type=%s",
            _storage_provider(),
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

                # Keep current Hostinger MinIO public URL behavior
                # so existing frontend/database URLs do not break.
                if _storage_provider() == "minio":
                    public_base = _env("MINIO_PUBLIC_BASE").rstrip("/")
                    if public_base:
                        return f"{public_base}/{bucket}/{object_name}"

                # AWS S3 or fallback: return presigned URL.
                return get_presigned_url(
                    bucket=bucket,
                    object_name=object_name,
                    expiry_seconds=3600,
                    public=True,
                )

            except Exception as e:
                logger.exception("Storage upload failed")
                raise RuntimeError(f"Storage upload failed: {str(e)}") from e

        image_url = await run_in_threadpool(_upload)

        logger.info("upload_activity_image success object=%s", object_name)
        return image_url

    except HTTPException:
        raise

    except Exception as e:
        logger.exception("upload_activity_image crashed")
        raise HTTPException(
            status_code=500,
            detail=f"Image upload failed: {str(e)}",
        )