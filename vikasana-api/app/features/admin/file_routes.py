from urllib.parse import urlparse, unquote
import os

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.core.dependencies import get_current_admin


router = APIRouter(prefix="/admin/files", tags=["Admin Files"])


BUCKET_MAP = {
    "activity_uploads": (
        os.getenv("MINIO_BUCKET_ACTIVITIES")
        or getattr(settings, "MINIO_BUCKET_ACTIVITIES", None)
        or "activity-uploads"
    ),
    "event_thumbnails": (
        os.getenv("EVENT_THUMBNAIL_BUCKET")
        or os.getenv("MINIO_BUCKET_EVENT_THUMBNAILS")
        or getattr(settings, "EVENT_THUMBNAIL_BUCKET", None)
        or getattr(settings, "MINIO_BUCKET_EVENT_THUMBNAILS", None)
        or "vikasana-event-thumbnails"
    ),
    "face_verification": (
        os.getenv("MINIO_FACE_BUCKET")
        or getattr(settings, "MINIO_FACE_BUCKET", None)
        or "face-verification"
    ),
    "certificates": (
        os.getenv("CERTIFICATE_BUCKET")
        or os.getenv("MINIO_BUCKET_CERTIFICATES")
        or getattr(settings, "CERTIFICATE_BUCKET", None)
        or getattr(settings, "MINIO_BUCKET_CERTIFICATES", None)
        or "vikasana-certificates"
    ),
    "faculty": (
        os.getenv("MINIO_BUCKET_FACULTY")
        or getattr(settings, "MINIO_BUCKET_FACULTY", None)
        or "vikasana-faculty"
    ),
}


LEGACY_PREFIXES = [
    "minio",
    "activity-uploads",
    "face-verification",
    "vikasana-certificates",
    "vikasana-event-thumbnails",
    "vikasana-faculty",
    "activity-uploads-652197206453-ap-south-1-an",
    "face-verification-652197206453-ap-south-1-an",
    "vikasana-certificates-652197206453-ap-south-1-an",
    "vikasana-event-thumbnails-652197206453-ap-south-1-an",
    "vikasana-faculty-652197206453-ap-south-1-an",
]


def clean_s3_key(value: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail="Missing file key")

    value = value.strip().replace("\\", "/").split("?")[0]

    if ".." in value:
        raise HTTPException(status_code=400, detail="Invalid file key")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = unquote(parsed.path).lstrip("/")

    value = value.lstrip("/")

    for prefix in LEGACY_PREFIXES:
        if value.startswith(prefix + "/"):
            value = value[len(prefix) + 1:]
            break

    if not value:
        raise HTTPException(status_code=400, detail="Invalid file key")

    return value


def get_s3_client():
    aws_region = (
        os.getenv("AWS_REGION")
        or getattr(settings, "AWS_REGION", None)
        or "ap-south-1"
    )

    access_key = (
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("MINIO_ACCESS_KEY")
        or os.getenv("MINIO_ROOT_USER")
        or getattr(settings, "AWS_ACCESS_KEY_ID", None)
        or getattr(settings, "MINIO_ACCESS_KEY", None)
        or getattr(settings, "MINIO_ROOT_USER", None)
    )

    secret_key = (
        os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("MINIO_SECRET_KEY")
        or os.getenv("MINIO_ROOT_PASSWORD")
        or getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
        or getattr(settings, "MINIO_SECRET_KEY", None)
        or getattr(settings, "MINIO_ROOT_PASSWORD", None)
    )

    if not access_key or not secret_key:
        raise RuntimeError("Missing AWS S3 credentials for signed URL generation")

    return boto3.client(
        "s3",
        region_name=aws_region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


@router.get("/signed-url")
async def get_admin_signed_file_url(
    bucket_type: str = Query(...),
    key: str = Query(...),
    admin=Depends(get_current_admin),
):
    bucket = BUCKET_MAP.get(bucket_type)

    if not bucket:
        raise HTTPException(status_code=400, detail="Invalid bucket type")

    object_key = clean_s3_key(key)

    try:
        print("ADMIN SIGNED URL bucket_type:", bucket_type)
        print("ADMIN SIGNED URL bucket:", bucket)
        print("ADMIN SIGNED URL key:", object_key)

        s3 = get_s3_client()

        signed_url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
            },
            ExpiresIn=300,
        )

        return {
            "url": signed_url,
            "expires_in": 300,
            "bucket_type": bucket_type,
            "bucket": bucket,
            "key": object_key,
        }

    except ClientError as exc:
        print("S3 signed URL ClientError:", exc)
        raise HTTPException(status_code=500, detail="Could not generate signed URL")

    except Exception as exc:
        print("S3 signed URL error:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))