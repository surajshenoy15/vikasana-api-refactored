from datetime import timedelta
import os

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, Query
from minio import Minio

from app.core.dependencies import get_current_student


router = APIRouter(prefix="/student/files", tags=["Student Files"])


BUCKET_MAP = {
    "event_thumbnails": os.getenv(
        "EVENT_THUMBNAIL_BUCKET",
        "vikasana-event-thumbnails",
    ),
    "certificates": os.getenv(
        "CERTIFICATE_BUCKET",
        "vikasana-certificates",
    ),
}


def get_s3_provider() -> str:
    return (os.getenv("S3_PROVIDER") or "minio").strip().lower()


def rewrite_minio_url_for_mobile(url: str) -> str:
    if not url:
        return url

    public_base = (
        os.getenv("MINIO_PUBLIC_BASE")
        or os.getenv("MINIO_PUBLIC_URL")
        or "https://api.vikasanafoundation.org/minio"
    ).rstrip("/")

    internal_bases = [
        "http://minio:9000",
        "https://minio:9000",
        "http://localhost:9000",
        "http://127.0.0.1:9000",
    ]

    for internal_base in internal_bases:
        if url.startswith(internal_base):
            return url.replace(internal_base, public_base, 1)

    return url


def get_minio_client() -> Minio:
    endpoint = os.getenv("MINIO_ENDPOINT", "s3.ap-south-1.amazonaws.com")
    access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    secure = os.getenv("MINIO_SECURE", "true").lower() == "true"

    if not access_key or not secret_key:
        raise HTTPException(
            status_code=500,
            detail="Storage credentials missing",
        )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def get_aws_s3_client():
    region = os.getenv("AWS_REGION", "ap-south-1")
    access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("MINIO_SECRET_KEY")

    if not access_key or not secret_key:
        raise HTTPException(
            status_code=500,
            detail="AWS storage credentials missing",
        )

    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )

def clean_object_key(key: str) -> str:
    clean_key = (key or "").strip().lstrip("/")

    if not clean_key:
        raise HTTPException(
            status_code=400,
            detail="Missing key",
        )

    if ".." in clean_key:
        raise HTTPException(
            status_code=400,
            detail="Invalid key",
        )

    # Remove old URL query params
    clean_key = clean_key.split("?")[0]

    # If full URL accidentally comes here, extract path
    if "://" in clean_key:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(clean_key)
            clean_key = parsed.path.lstrip("/")
        except Exception:
            pass

    legacy_prefixes = [
        "minio/",
        "vikasana-event-thumbnails/",
        "vikasana-event-thumbnails-652197206453-ap-south-1-an/",
        "vikasana-certificates/",
        "vikasana-certificates-652197206453-ap-south-1-an/",
    ]

    for prefix in legacy_prefixes:
        if clean_key.startswith(prefix):
            clean_key = clean_key[len(prefix):]
            break

    return clean_key


@router.get("/signed-url")
def get_student_signed_url(
    bucket_type: str = Query(...),
    key: str = Query(...),
    current_student=Depends(get_current_student),
):
    bucket = BUCKET_MAP.get(bucket_type)

    if not bucket:
        raise HTTPException(
            status_code=400,
            detail="Invalid bucket_type",
        )

    clean_key = clean_object_key(key)

    try:
        provider = get_s3_provider()

        print("SIGNED URL PROVIDER:", provider)
        print("SIGNED URL BUCKET:", bucket)
        print("SIGNED URL KEY:", clean_key)

        if provider == "aws":
            s3 = get_aws_s3_client()

            signed_url = s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": bucket,
                    "Key": clean_key,
                },
                ExpiresIn=900,
            )

            return {"url": signed_url}

        client = get_minio_client()

        signed_url = client.presigned_get_object(
            bucket_name=bucket,
            object_name=clean_key,
            expires=timedelta(minutes=15),
        )

        return {"url": rewrite_minio_url_for_mobile(signed_url)}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create signed URL: {str(e)}",
        )