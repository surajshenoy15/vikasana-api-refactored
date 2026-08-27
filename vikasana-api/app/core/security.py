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
def create_access_token(
    admin_id: int,
    email: str,
    *,
    role: str | None = None,
    session_id: str | None = None,
    token_version: int | None = None,
    expires_minutes: int | None = None,
) -> str:
    """
    Create a signed access JWT.

    Backward compatibility:
    - Existing Faculty calls can continue passing only
      admin_id + email.
    - Session/security claims are added only when provided.

    Admin/Super Admin login will pass:
    - role
    - session_id (sid)
    - token_version (tv)
    - expires_minutes
    """

    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(admin_id),
        "email": email,
        "type": "access",
        "iat": int(now.timestamp()),
    }

    if role is not None:
        payload["role"] = role

    if session_id is not None:
        payload["sid"] = session_id

    if token_version is not None:
        payload["tv"] = int(token_version)

    if expires_minutes is not None:
        payload["exp"] = (
            now
            + timedelta(
                minutes=expires_minutes
            )
        )

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

def decode_access_token(token: str) -> dict:
    """
    Decodes and verifies JWT signature + expiry.
    Raises jose.JWTError on any failure.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])