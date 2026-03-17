from __future__ import annotations

import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.minio_client import get_minio

router = APIRouter(prefix="/public/minio", tags=["Public - MinIO Proxy"])


@router.get("/object")
async def get_object(
    bucket: str = Query(..., min_length=1),
    object_name: str = Query(..., min_length=1),
):
    """
    ✅ HTTPS-safe proxy for MinIO objects.
    Browser loads from API domain (HTTPS), so NO mixed content.

    Example:
      /api/public/minio/object?bucket=vikasana-event-thumbnails&object_name=thumbnails/2/abc.png
    """
    try:
        m = get_minio()

        # stat to confirm existence + get content-type if available
        st = m.stat_object(bucket, object_name)
        content_type = getattr(st, "content_type", None) or mimetypes.guess_type(object_name)[0] or "application/octet-stream"

        obj = m.get_object(bucket, object_name)

        # Stream response
        filename = object_name.split("/")[-1] or "file"
        headers = {
            "Content-Type": content_type,
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "public, max-age=300",  # 5 min cache (tune as needed)
        }

        return StreamingResponse(obj.stream(32 * 1024), headers=headers)

    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Object not found: {bucket}/{object_name}")