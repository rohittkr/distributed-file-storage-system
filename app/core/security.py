from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from pwdlib import PasswordHash

from app.core.config import settings


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a user password using the recommended Argon2 configuration."""
    return password_hash.hash(password)


def verify_password(password: str, password_hash_value: str) -> bool:
    """Verify a plaintext password against a stored password hash."""
    try:
        return password_hash.verify(password, password_hash_value)
    except Exception:
        return False


def create_access_token(subject: str) -> str:
    """Create a signed JWT access token with an expiration timestamp."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_minutes)

    payload = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": expires,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> str | None:
    """Validate a JWT access token and return its subject."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )

        if payload.get("type") != "access":
            return None

        subject = payload.get("sub")

        if subject is None:
            return None

        subject = str(subject)

        if not subject.isdigit():
            return None

        return subject

    except (JWTError, ValueError, TypeError):
        return None