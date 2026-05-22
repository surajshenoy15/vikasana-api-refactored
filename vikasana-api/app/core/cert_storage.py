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

    Important:
    Do NOT generate using internal minio:9000 and replace string.
    Sign directly using public endpoint:
      https://minio.vikasanafoundation.org/...
    """

    if expires_in < 60:
        expires_in = 60

    if expires_in > 7 * 24 * 3600:
        expires_in = 7 * 24 * 3600

    clean_key = normalize_certificate_object_key(object_key)

    if not clean_key:
        raise RuntimeError("Certificate object key is empty")

    try:
        public_minio = get_public_minio()

        return public_minio.presigned_get_object(
            bucket_name=MINIO_BUCKET_CERTIFICATES,
            object_name=clean_key,
            expires=timedelta(seconds=int(expires_in)),
        )

    except S3Error as e:
        raise RuntimeError(f"MinIO certificate presign failed: {e}") from e