from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

from app.core.config import settings


def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
    payload = data.copy()
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload.update({"exp": exp})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])