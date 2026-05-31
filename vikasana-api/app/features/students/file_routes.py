from datetime import timedelta
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from minio import Minio

from app.core.dependencies import get_current_student


router = APIRouter(prefix="/student/files", tags=["Student Files"])


BUCKET_MAP = {
    "event_thumbnails": os.getenv(
        "EVENT_THUMBNAIL_BUCKET",
        "vikasana-event-thumbnails-652197206453-ap-south-1-an",
    ),
    "certificates": os.getenv(
        "CERTIFICATE_BUCKET",
        "vikasana-certificates-652197206453-ap-south-1-an",
    ),
}
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
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
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

    clean_key = key.strip().lstrip("/")

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

    try:
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
