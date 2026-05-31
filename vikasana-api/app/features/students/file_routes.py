from fastapi import APIRouter, Depends, HTTPException, Query
from minio import Minio
from datetime import timedelta
import os

from app.core.security import get_current_student

router = APIRouter(prefix="/student/files", tags=["Student Files"])

BUCKET_MAP = {
    "event_thumbnails": os.getenv(
        "EVENT_THUMBNAIL_BUCKET",
        "vikasana-event-thumbnails-652197206453-ap-south-1-an"
    ),
    "certificates": os.getenv(
        "CERTIFICATE_BUCKET",
        "vikasana-certificates-652197206453-ap-south-1-an"
    ),
}

def get_minio_client():
    endpoint = os.getenv("MINIO_ENDPOINT", "s3.ap-south-1.amazonaws.com")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    secure = os.getenv("MINIO_SECURE", "true").lower() == "true"

    if not access_key or not secret_key:
        raise HTTPException(status_code=500, detail="Storage credentials missing")

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
        raise HTTPException(status_code=400, detail="Invalid bucket_type")

    clean_key = key.strip().lstrip("/")

    if ".." in clean_key:
        raise HTTPException(status_code=400, detail="Invalid key")

    try:
        client = get_minio_client()
        url = client.presigned_get_object(
            bucket,
            clean_key,
            expires=timedelta(minutes=15),
        )
        return {"url": url}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create signed URL: {str(e)}"
        )