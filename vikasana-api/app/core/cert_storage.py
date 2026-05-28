# app/core/cert_storage.py

import os
from io import BytesIO
from datetime import timedelta

from minio.error import S3Error

from app.core.minio_client import get_minio, get_public_minio, ensure_bucket as ensure_storage_bucket


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _storage_provider() -> str:
    return _env("S3_PROVIDER", "minio").lower()


def _certificate_bucket() -> str:
    """
    Uses MinIO bucket in current Hostinger setup.
    Uses AWS bucket when S3_PROVIDER=aws later.
    """
    if _storage_provider() == "aws":
        return _env("AWS_S3_BUCKET_CERTIFICATES", "vikasana-certificates")

    return _env("MINIO_BUCKET_CERTIFICATES", "vikasana-certificates")


def get_certificate_bucket() -> str:
    return _certificate_bucket()


def ensure_bucket() -> None:
    bucket = _certificate_bucket()
    try:
        minio = get_minio()
        ensure_storage_bucket(minio, bucket)
    except S3Error as e:
        raise RuntimeError(f"Certificate bucket ensure failed: {e}") from e


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

    # Remove old minio prefix if present
    if key.startswith("minio/"):
        key = key[len("minio/") :]

    # Remove possible bucket prefix if present
    bucket = _certificate_bucket()
    bucket_prefix = f"{bucket}/"
    if key.startswith(bucket_prefix):
        key = key[len(bucket_prefix) :]

    # Remove MinIO/AWS bucket env aliases if present accidentally
    possible_bucket_prefixes = {
        _env("MINIO_BUCKET_CERTIFICATES", "vikasana-certificates"),
        _env("AWS_S3_BUCKET_CERTIFICATES", "vikasana-certificates"),
    }

    for bucket_name in possible_bucket_prefixes:
        prefix = f"{bucket_name}/"
        if key.startswith(prefix):
            key = key[len(prefix) :]

    return key


def upload_certificate_pdf_bytes(cert_id: int, pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise RuntimeError("Certificate PDF bytes are empty")

    ensure_bucket()

    bucket = _certificate_bucket()
    object_key = build_object_key(cert_id)
    data = BytesIO(pdf_bytes)
    size = len(pdf_bytes)

    try:
        minio = get_minio()

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


def presign_certificate_download_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Returns browser-safe presigned certificate URL.

    For MinIO:
      signs using MINIO_PUBLIC_ENDPOINT

    For AWS:
      signs using AWS S3 endpoint
    """

    expires_in = max(60, min(int(expires_in), 7 * 24 * 3600))

    clean_key = normalize_certificate_object_key(object_key)

    if not clean_key:
        raise RuntimeError("Certificate object key is empty")

    bucket = _certificate_bucket()

    try:
        public_minio = get_public_minio()

        return public_minio.presigned_get_object(
            bucket_name=bucket,
            object_name=clean_key,
            expires=timedelta(seconds=expires_in),
        )

    except S3Error as e:
        raise RuntimeError(f"Certificate presign failed: {e}") from e