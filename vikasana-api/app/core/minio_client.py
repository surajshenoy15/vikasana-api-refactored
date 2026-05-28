import os
from datetime import timedelta
from minio import Minio


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "y", "on")


def _storage_provider() -> str:
    """
    Supported values:
    - minio: Hostinger/local MinIO
    - aws: AWS S3
    """
    return _env("S3_PROVIDER", "minio").lower()


def _aws_s3_endpoint() -> str:
    region = _env("AWS_REGION", "ap-south-1")
    return _env("AWS_S3_ENDPOINT", f"s3.{region}.amazonaws.com")


def _get_access_key() -> str:
    if _storage_provider() == "aws":
        return (
            _env("AWS_ACCESS_KEY_ID")
            or _env("MINIO_ACCESS_KEY")
            or _env("MINIO_ROOT_USER")
        )

    return _env("MINIO_ACCESS_KEY") or _env("MINIO_ROOT_USER")


def _get_secret_key() -> str:
    if _storage_provider() == "aws":
        return (
            _env("AWS_SECRET_ACCESS_KEY")
            or _env("MINIO_SECRET_KEY")
            or _env("MINIO_ROOT_PASSWORD")
        )

    return _env("MINIO_SECRET_KEY") or _env("MINIO_ROOT_PASSWORD")


def get_minio() -> Minio:
    """
    Internal storage client.
    Used for backend upload/read operations.
    """
    provider = _storage_provider()

    if provider == "aws":
        endpoint = _aws_s3_endpoint()
        secure = True
    else:
        endpoint = _env("MINIO_ENDPOINT", "minio:9000")
        secure = _env_bool("MINIO_SECURE", "false")

    access_key = _get_access_key()
    secret_key = _get_secret_key()

    if not access_key or not secret_key:
        raise RuntimeError(
            f"Storage credentials missing for provider={provider}. "
            "Check MINIO_ACCESS_KEY/MINIO_SECRET_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def get_public_minio() -> Minio:
    """
    Public storage client.
    Used for generating browser/admin accessible presigned URLs.
    """
    provider = _storage_provider()

    if provider == "aws":
        endpoint = _aws_s3_endpoint()
        secure = True
    else:
        endpoint = _env("MINIO_PUBLIC_ENDPOINT", "minio.vikasanafoundation.org")
        secure = _env_bool("MINIO_PUBLIC_SECURE", "true")

    access_key = _get_access_key()
    secret_key = _get_secret_key()

    if not access_key or not secret_key:
        raise RuntimeError(
            f"Public storage credentials missing for provider={provider}. "
            "Check MINIO_ACCESS_KEY/MINIO_SECRET_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def ensure_bucket(minio: Minio, bucket: str) -> None:
    if not bucket:
        raise ValueError("bucket is required")

    found = minio.bucket_exists(bucket)
    if not found:
        minio.make_bucket(bucket)


def get_presigned_url(
    bucket: str,
    object_name: str,
    expiry_seconds: int = 600,
    public: bool = True,
) -> str:
    if not bucket:
        raise ValueError("bucket is required")

    if not object_name:
        raise ValueError("object_name is required")

    expiry_seconds = max(60, min(int(expiry_seconds), 3600))

    client = get_public_minio() if public else get_minio()

    return client.presigned_get_object(
        bucket_name=bucket,
        object_name=object_name,
        expires=timedelta(seconds=expiry_seconds),
    )