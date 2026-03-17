from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings

# ── Bcrypt Password Hashing ───────────────────────────────────────────
# "deprecated=auto" → old hashes are silently re-hashed on next login
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Constant-time dummy hash used when no real hash exists,
# prevents timing attacks that could reveal valid emails.
_DUMMY_HASH = "$2b$12$KIXa8pRj6u8OjKvI7bQsqOEkBqYHqFbY3Ku.Fsp7p/e8XGJ0XOGK6"


def hash_password(plain: str) -> str:
    """
    Hash a plaintext password with bcrypt via passlib.
    bcrypt automatically generates a unique salt — same password gives
    a different hash each time, which is correct and expected.
    """
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """
    Timing-safe bcrypt comparison via passlib.

    Guards against:
      - None hash  (account has no password set yet)
      - Truncated / malformed hash  (DB column too narrow, or written by
        raw bcrypt instead of passlib — both produce a ValueError in passlib)
      - Timing attacks  (always runs a bcrypt verify, even on dummy hash)
    """
    if not hashed or len(hashed) < 59:
        # Run dummy verify so response time is identical — prevents
        # attackers from detecting "no password set" via timing.
        pwd_context.verify(plain, _DUMMY_HASH)
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        # Malformed hash slipped through length check — still safe
        pwd_context.verify(plain, _DUMMY_HASH)
        return False


# ── JWT Token ─────────────────────────────────────────────────────────
def create_access_token(admin_id: int, email: str) -> str:
    """
    Creates a signed JWT. Change SECRET_KEY in .env to invalidate all tokens.
    Change ACCESS_TOKEN_EXPIRE_MINUTES in .env to adjust session length.

    Payload contains:
      sub   — admin ID (standard JWT claim)
      email — for frontend display
      type  — guards against using wrong token types
      iat   — issued at
      exp   — expiry (set by ACCESS_TOKEN_EXPIRE_MINUTES in .env)
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub":   str(admin_id),
        "email": email,
        "type":  "access",
        "iat":   now,
        "exp":   now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decodes and verifies JWT signature + expiry.
    Raises jose.JWTError on any failure.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])