from pydantic import BaseModel


class ThumbnailUploadOut(BaseModel):
    public_url: str
    object_name: str
    content_type: str
    size: int