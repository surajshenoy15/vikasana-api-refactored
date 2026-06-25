from datetime import datetime, timedelta, timezone
from jose import jwt

from app.core.config import settings


def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
    payload = data.copy()

    exp = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload.update({
        "exp": exp,
        "token_type": "access"
    })

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )


def create_refresh_token(data: dict, expires_days: int | None = None) -> str:
    payload = data.copy()

    exp = datetime.now(timezone.utc) + timedelta(
        days=expires_days or settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    payload.update({
        "exp": exp,
        "token_type": "refresh"
    })

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM]
    )