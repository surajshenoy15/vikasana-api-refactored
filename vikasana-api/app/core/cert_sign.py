import hmac
import hashlib
from app.core.config import settings


def sign_cert(cert_id: str) -> str:
    """
    Sign certificate identifier (prefer certificate_no string).
    Always treat as string.
    """
    key = settings.CERT_SIGNING_SECRET.encode("utf-8")
    msg = str(cert_id).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_sig(cert_id: str, sig: str) -> bool:
    """
    Verify HMAC signature safely.
    """
    if not sig:
        return False

    expected = sign_cert(cert_id)
    return hmac.compare_digest(expected, sig)