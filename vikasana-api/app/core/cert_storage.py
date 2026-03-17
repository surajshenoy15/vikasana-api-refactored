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
    return v


def _env_bool(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


MINIO_ENDPOINT = _env("MINIO_ENDPOINT")  # e.g. "31.97.230.171:9000"
MINIO_ACCESS_KEY = _env("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = _env("MINIO_SECRET_KEY")
MINIO_USE_SSL = _env_bool("MINIO_USE_SSL", "false")

MINIO_BUCKET_CERTIFICATES = _env("MINIO_BUCKET_CERTIFICATES")  # e.g. "certificates"

_minio = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_USE_SSL,
)


def ensure_bucket() -> None:
    try:
        if not _minio.bucket_exists(MINIO_BUCKET_CERTIFICATES):
            _minio.make_bucket(MINIO_BUCKET_CERTIFICATES)
    except S3Error as e:
        raise RuntimeError(f"MinIO bucket ensure failed: {e}") from e


def build_object_key(cert_id: int) -> str:
    # Stored in DB: certificates/cert_12.pdf
    return f"certificates/cert_{int(cert_id)}.pdf"


def upload_certificate_pdf_bytes(cert_id: int, pdf_bytes: bytes) -> str:
    """
    ✅ Matches your events_controller usage:
        object_key = upload_certificate_pdf_bytes(cert.id, pdf_bytes)

    Uploads PDF bytes to MinIO and returns object_key stored in DB.
    """
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

    return object_key


def presign_certificate_download_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Returns a presigned URL for React Native download/open.
    """
    if expires_in < 60:
        expires_in = 60
    if expires_in > 7 * 24 * 3600:
        expires_in = 7 * 24 * 3600

    try:
        return _minio.presigned_get_object(
            bucket_name=MINIO_BUCKET_CERTIFICATES,
            object_name=object_key,
            expires=timedelta(seconds=int(expires_in)),
        )
    except S3Error as e:
        raise RuntimeError(f"MinIO presign failed: {e}") from e