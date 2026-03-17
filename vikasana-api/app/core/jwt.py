from datetime import datetime, timedelta, timezone
from jose import jwt

from app.core.config import settings


def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
    payload = data.copy()
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload.update({"exp": exp})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)