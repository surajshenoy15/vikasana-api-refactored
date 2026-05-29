# app/core/cert_storage.py

import os
from io import BytesIO
from datetime import timedelta

from minio import Minio
from minio.error import S3Error


def _env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v.strip()


def _env_optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


# Internal MinIO endpoint for Docker backend upload
MINIO_ENDPOINT = _env("MINIO_ENDPOINT")  # minio:9000
MINIO_ACCESS_KEY = _env("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = _env("MINIO_SECRET_KEY")

# Use MINIO_SECURE, not MINIO_USE_SSL
MINIO_SECURE = _env_bool("MINIO_SECURE", "false")

MINIO_BUCKET_CERTIFICATES = _env("MINIO_BUCKET_CERTIFICATES")

# Public MinIO endpoint for browser-safe presigned URLs
# Correct:
# MINIO_PUBLIC_ENDPOINT=minio.vikasanafoundation.org
# MINIO_PUBLIC_SECURE=true
MINIO_PUBLIC_ENDPOINT = _env_optional(
    "MINIO_PUBLIC_ENDPOINT",
    "minio.vikasanafoundation.org",
)

MINIO_PUBLIC_SECURE = _env_bool("MINIO_PUBLIC_SECURE", "true")


# Internal client: upload/read inside Docker network
_minio = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)


def get_public_minio() -> Minio:
    """
    Public MinIO client used only for generating browser-accessible signed URLs.
    Do not include https:// in MINIO_PUBLIC_ENDPOINT.
    """
    return Minio(
        MINIO_PUBLIC_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_PUBLIC_SECURE,
    )


def ensure_bucket() -> None:
    try:
        if not _minio.bucket_exists(MINIO_BUCKET_CERTIFICATES):
            _minio.make_bucket(MINIO_BUCKET_CERTIFICATES)
    except S3Error as e:
        raise RuntimeError(f"MinIO bucket ensure failed: {e}") from e


def build_object_key(cert_id: int) -> str:
    return f"certificates/cert_{int(cert_id)}.pdf"


def normalize_certificate_object_key(object_key: str) -> str:
    """
    Correct object key should be:
      certificates/cert_29.pdf

    Not:
      vikasana-certificates/certificates/cert_29.pdf
    """
    key = str(object_key or "").strip().lstrip("/")

    # Remove query params if accidentally passed full URL/key with query
    key = key.split("?")[0]

    # If full URL was accidentally stored, keep only path
    if "://" in key:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(key)
            key = parsed.path.lstrip("/")
        except Exception:
            pass

    # Remove minio prefix if present
    if key.startswith("minio/"):
        key = key[len("minio/") :]

    # Remove bucket prefix if present
    bucket_prefix = f"{MINIO_BUCKET_CERTIFICATES}/"
    if key.startswith(bucket_prefix):
        key = key[len(bucket_prefix) :]

    return key


def upload_certificate_pdf_bytes(cert_id: int, pdf_bytes: bytes) -> str:
    ensure_bucket()

    object_key = build_object_key(cert_id)
    data = BytesIO(pdf_bytes)
    size = len(pdf_bytes)

    try:
        _minio.put_object(
            bucket_name=MINIO_BUCKET_CERTIFICATES,
            object_name=object_key,
            data=data,
            length=size,
            content_type="application/pdf",
        )
    except S3Error as e:
        raise RuntimeError(f"MinIO put_object failed: {e}") from e

    # Store only object key in DB
    return object_key


def presign_certificate_download_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Returns browser-safe presigned certificate URL.
    AWS test/prod: use boto3 S3 presigned URL.
    MinIO local/live VPS: keep old MinIO presigned URL.
    """
    import os
    import boto3
    from botocore.config import Config

    if not object_key:
        raise RuntimeError("Certificate object key is missing")

    object_key = str(object_key).strip().replace("\\", "/").split("?")[0]
    object_key = object_key.lstrip("/")

    # If DB stores full URL, convert it to path only
    if object_key.startswith("http://") or object_key.startswith("https://"):
        from urllib.parse import urlparse, unquote
        parsed = urlparse(object_key)
        object_key = unquote(parsed.path).lstrip("/")

    # Remove bucket name if stored inside path
    bucket_prefix = f"{MINIO_BUCKET_CERTIFICATES}/"
    if object_key.startswith(bucket_prefix):
        object_key = object_key[len(bucket_prefix):]

    # Remove old MinIO proxy prefixes if present
    legacy_prefixes = [
        "minio/",
        "vikasana-certificates/",
        "vikasana-certificates-652197206453-ap-south-1-an/",
    ]

    for prefix in legacy_prefixes:
        if object_key.startswith(prefix):
            object_key = object_key[len(prefix):]
            break

    s3_provider = (
        os.getenv("S3_PROVIDER")
        or getattr(settings, "S3_PROVIDER", "")
        if "settings" in globals()
        else os.getenv("S3_PROVIDER")
    )

    if str(s3_provider).lower() == "aws":
        aws_region = os.getenv("AWS_REGION") or "ap-south-1"

        access_key = (
            os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("MINIO_ACCESS_KEY")
            or os.getenv("MINIO_ROOT_USER")
        )

        secret_key = (
            os.getenv("AWS_SECRET_ACCESS_KEY")
            or os.getenv("MINIO_SECRET_KEY")
            or os.getenv("MINIO_ROOT_PASSWORD")
        )

        if not access_key or not secret_key:
            raise RuntimeError("Missing AWS credentials for certificate signed URL")

        s3 = boto3.client(
            "s3",
            region_name=aws_region,
            endpoint_url=f"https://s3.{aws_region}.amazonaws.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
            ),
        )

        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": MINIO_BUCKET_CERTIFICATES,
                "Key": object_key,
                "ResponseContentDisposition": f'attachment; filename="{object_key.split("/")[-1]}"',
            },
            ExpiresIn=expires_in,
        )

    # Existing MinIO/live VPS behavior
    try:
        return public_minio.presigned_get_object(
            bucket_name=MINIO_BUCKET_CERTIFICATES,
            object_name=object_key,
            expires=timedelta(seconds=expires_in),
        )
    except Exception as e:
        raise RuntimeError(f"MinIO certificate presign failed: {e}") from e
