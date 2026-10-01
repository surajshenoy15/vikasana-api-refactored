# app/core/cert_storage.py

import os
from io import BytesIO
from datetime import timedelta
from urllib.parse import urlparse, unquote

import boto3
from botocore.config import Config
from minio import Minio
from minio.error import S3Error



def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )


def _certificate_minio_credentials():
    access_key = (
        _env("MINIO_ACCESS_KEY")
        or _env("MINIO_ROOT_USER")
    )

    secret_key = (
        _env("MINIO_SECRET_KEY")
        or _env("MINIO_ROOT_PASSWORD")
    )

    if not access_key or not secret_key:
        raise RuntimeError(
            "MinIO credentials missing for certificate storage"
        )

    return access_key, secret_key


def _certificate_internal_minio() -> Minio:
    """
    Internal VPS MinIO client used for certificate uploads/checks.

    This intentionally ignores global S3_PROVIDER.
    """
    endpoint = _env(
        "MINIO_ENDPOINT",
        "minio:9000",
    )

    endpoint = (
        endpoint
        .replace("http://", "")
        .replace("https://", "")
        .rstrip("/")
    )

    access_key, secret_key = (
        _certificate_minio_credentials()
    )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=_env_bool(
            "MINIO_SECURE",
            "false",
        ),
    )


def _certificate_public_minio() -> Minio:
    """
    Public MinIO client used only for browser-safe certificate URLs.

    This intentionally ignores global S3_PROVIDER.
    """
    endpoint = _env(
        "MINIO_PUBLIC_ENDPOINT",
        "minio.vikasanafoundation.org",
    )

    endpoint = (
        endpoint
        .replace("http://", "")
        .replace("https://", "")
        .rstrip("/")
    )

    access_key, secret_key = (
        _certificate_minio_credentials()
    )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=_env_bool(
            "MINIO_PUBLIC_SECURE",
            "true",
        ),
    )


def _storage_provider() -> str:
    """
    Certificate storage provider.

    Certificates can use VPS MinIO independently while other
    legacy application media may still use the global S3 provider.
    """
    return _env(
        "CERTIFICATE_STORAGE_PROVIDER",
        _env("S3_PROVIDER", "minio"),
    ).lower()


def _certificate_bucket() -> str:
    """
    MinIO/live VPS:
      MINIO_BUCKET_CERTIFICATES or vikasana-certificates

    AWS event/prod:
      AWS_S3_BUCKET_CERTIFICATES or vikasana-certificates-652197206453-ap-south-1-an
    """
    if _storage_provider() == "aws":
        return _env(
            "AWS_S3_BUCKET_CERTIFICATES",
            "vikasana-certificates-652197206453-ap-south-1-an",
        )

    return _env("MINIO_BUCKET_CERTIFICATES", "vikasana-certificates")


def get_certificate_bucket() -> str:
    return _certificate_bucket()


def ensure_bucket() -> None:
    bucket = _certificate_bucket()

    try:
        minio = _certificate_internal_minio()

        if not minio.bucket_exists(bucket):
            minio.make_bucket(bucket)

    except S3Error as e:
        raise RuntimeError(
            f"Certificate bucket ensure failed: {e}"
        ) from e


def build_object_key(cert_id: int) -> str:
    return f"certificates/cert_{int(cert_id)}.pdf"


def normalize_certificate_object_key(object_key: str) -> str:
    """
    Correct object key should be:
      certificates/cert_29.pdf

    Not:
      vikasana-certificates/certificates/cert_29.pdf
      vikasana-certificates-652197206453-ap-south-1-an/certificates/cert_29.pdf
      /minio/vikasana-certificates/certificates/cert_29.pdf
      https://.../bucket/certificates/cert_29.pdf
    """
    key = str(object_key or "").strip().replace("\\", "/").lstrip("/")

    if not key:
        return ""

    # Remove query params
    key = key.split("?")[0]

    # If full URL was passed, keep only path
    if key.startswith("http://") or key.startswith("https://"):
        parsed = urlparse(key)
        key = unquote(parsed.path).lstrip("/")

    # Remove old proxy prefix
    if key.startswith("minio/"):
        key = key[len("minio/") :]

    # Remove possible bucket prefixes
    possible_bucket_names = {
        _certificate_bucket(),
        _env("MINIO_BUCKET_CERTIFICATES", "vikasana-certificates"),
        _env(
            "AWS_S3_BUCKET_CERTIFICATES",
            "vikasana-certificates-652197206453-ap-south-1-an",
        ),
        "vikasana-certificates",
        "vikasana-certificates-652197206453-ap-south-1-an",
    }

    for bucket_name in possible_bucket_names:
        if not bucket_name:
            continue

        prefix = f"{bucket_name}/"
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break

    return key.lstrip("/")


def upload_certificate_pdf_bytes(cert_id: int, pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise RuntimeError("Certificate PDF bytes are empty")

    ensure_bucket()

    bucket = _certificate_bucket()
    object_key = build_object_key(cert_id)
    data = BytesIO(pdf_bytes)
    size = len(pdf_bytes)

    try:
        minio = _certificate_internal_minio()

        minio.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=data,
            length=size,
            content_type="application/pdf",
        )

    except S3Error as e:
        raise RuntimeError(f"Certificate put_object failed: {e}") from e

    # Store only object key in DB
    return object_key


def _aws_presign_certificate_url(
    bucket: str,
    object_key: str,
    expires_in: int,
) -> str:
    aws_region = (
        _env("AWS_REGION")
        or _env("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )

    access_key = (
        _env("AWS_ACCESS_KEY_ID")
        or _env("MINIO_ACCESS_KEY")
        or _env("MINIO_ROOT_USER")
    )

    secret_key = (
        _env("AWS_SECRET_ACCESS_KEY")
        or _env("MINIO_SECRET_KEY")
        or _env("MINIO_ROOT_PASSWORD")
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

    filename = object_key.split("/")[-1] or "certificate.pdf"

    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket,
            "Key": object_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires_in,
    )


def _minio_presign_certificate_url(
    bucket: str,
    object_key: str,
    expires_in: int,
) -> str:
    try:
        public_minio = _certificate_public_minio()

        return public_minio.presigned_get_object(
            bucket_name=bucket,
            object_name=object_key,
            expires=timedelta(seconds=expires_in),
        )

    except S3Error as e:
        raise RuntimeError(f"Certificate presign failed: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Certificate presign failed: {e}") from e


def presign_certificate_download_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Returns browser-safe presigned certificate URL.

    MinIO/live VPS:
      Uses public MinIO presigned URL.

    AWS event/prod:
      Uses boto3 S3 presigned URL.

    DB should store only:
      certificates/cert_29.pdf
    """
    if not object_key:
        raise RuntimeError("Certificate object key is missing")

    expires_in = max(60, min(int(expires_in), 7 * 24 * 3600))

    bucket = _certificate_bucket()
    clean_key = normalize_certificate_object_key(object_key)

    if not clean_key:
        raise RuntimeError("Certificate object key is invalid")

    if ".." in clean_key:
        raise RuntimeError("Certificate object key is invalid")

    if _storage_provider() == "aws":
        return _aws_presign_certificate_url(
            bucket=bucket,
            object_key=clean_key,
            expires_in=expires_in,
        )

    return _minio_presign_certificate_url(
        bucket=bucket,
        object_key=clean_key,
        expires_in=expires_in,
    )