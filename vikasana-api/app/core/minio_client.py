import os
from datetime import timedelta
from minio import Minio


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "y", "on")


# Internal Docker client: used for upload/read inside backend containers
def get_minio() -> Minio:
    endpoint = _env("MINIO_ENDPOINT", "minio:9000")
    access_key = _env("MINIO_ACCESS_KEY") or _env("MINIO_ROOT_USER")
    secret_key = _env("MINIO_SECRET_KEY") or _env("MINIO_ROOT_PASSWORD")
    secure = _env_bool("MINIO_SECURE", "false")

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


# Public client: used ONLY for generating browser/admin accessible presigned URLs
def get_public_minio() -> Minio:
    endpoint = _env("MINIO_PUBLIC_ENDPOINT", "minio.vikasanafoundation.org")
    access_key = _env("MINIO_ACCESS_KEY") or _env("MINIO_ROOT_USER")
    secret_key = _env("MINIO_SECRET_KEY") or _env("MINIO_ROOT_PASSWORD")
    secure = _env_bool("MINIO_PUBLIC_SECURE", "true")

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def ensure_bucket(minio: Minio, bucket: str) -> None:
    found = minio.bucket_exists(bucket)
    if not found:
        minio.make_bucket(bucket)


def get_presigned_url(
    bucket: str,
    object_name: str,
    expiry_seconds: int = 600,
    public: bool = True,
) -> str:
    if not object_name:
        raise ValueError("object_name is required")

    if expiry_seconds < 60:
        expiry_seconds = 60

    if expiry_seconds > 3600:
        expiry_seconds = 3600

    client = get_public_minio() if public else get_minio()

    return client.presigned_get_object(
        bucket_name=bucket,
        object_name=object_name,
        expires=timedelta(seconds=int(expiry_seconds)),
    )