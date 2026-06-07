# app/features/face/enrollment_storage.py

import os
import uuid
import boto3
from botocore.client import Config


def _env(name: str, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_face_bucket() -> str:
    bucket = (
        _env("AWS_S3_BUCKET_FACE")
        or _env("MINIO_FACE_BUCKET")
        or _env("FACE_BUCKET")
    )
    if not bucket:
        raise RuntimeError(
            "Face bucket not configured. Set AWS_S3_BUCKET_FACE or MINIO_FACE_BUCKET."
        )
    return bucket


def get_s3_client():
    provider = (_env("S3_PROVIDER", "minio") or "minio").lower()
    region = _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "ap-south-1"))

    if provider == "aws":
        return boto3.client(
            "s3",
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    endpoint_url = (
        _env("MINIO_ENDPOINT")
        or _env("MINIO_URL")
        or _env("S3_ENDPOINT_URL")
        or "http://minio:9000"
    )

    access_key = (
        _env("MINIO_ACCESS_KEY")
        or _env("AWS_ACCESS_KEY_ID")
        or "minioadmin"
    )

    secret_key = (
        _env("MINIO_SECRET_KEY")
        or _env("AWS_SECRET_ACCESS_KEY")
        or "minioadmin"
    )

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def upload_face_enrollment_image(
    *,
    student_id: int,
    slot: int,
    contents: bytes,
    content_type: str = "image/jpeg",
) -> dict:
    bucket = get_face_bucket()
    client = get_s3_client()

    ext = "jpg"
    if content_type:
        ct = content_type.lower()
        if "png" in ct:
            ext = "png"
        elif "webp" in ct:
            ext = "webp"
        elif "jpeg" in ct or "jpg" in ct:
            ext = "jpg"

    object_key = f"face-enrollment/{student_id}/{slot}-{uuid.uuid4().hex}.{ext}"

    client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=contents,
        ContentType=content_type or "image/jpeg",
    )

    return {
        "bucket": bucket,
        "key": object_key,
        "url": f"s3://{bucket}/{object_key}",
    }


def create_face_image_signed_url(image_key: str, expires_in: int = 900) -> str | None:
    if not image_key:
        return None

    bucket = get_face_bucket()
    client = get_s3_client()

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": image_key,
        },
        ExpiresIn=expires_in,
    )


def delete_face_image_key(image_key: str):
    if not image_key:
        return

    bucket = get_face_bucket()
    client = get_s3_client()

    try:
        client.delete_object(
            Bucket=bucket,
            Key=image_key,
        )
    except Exception as e:
        print(f"⚠️ Failed to delete face enrollment image {image_key}: {e}")