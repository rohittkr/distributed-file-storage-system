from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from pwdlib import PasswordHash

from app.core.config import settings


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a user password using the recommended Argon2 configuration."""
    return password_hash.hash(password)


def verify_password(password: str, password_hash_value: str) -> bool:
    """Verify a plaintext password against its stored password hash."""
    return password_hash.verify(password, password_hash_value)


def create_access_token(subject: str) -> str:
    """Create a signed JWT access token."""
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_minutes
    )

    payload = {
        "sub": subject,
        "exp": expires,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> str | None:
    """Validate a JWT and return its subject, or None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )

        subject = payload.get("sub")

        if subject is None:
            return None

        return str(subject)

    except JWTError:
        return None