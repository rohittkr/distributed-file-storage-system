from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing():
    password = "correct horse battery staple"

    hashed = hash_password(password)

    assert hashed != password
    assert hashed.startswith("$argon2")
    assert verify_password(password, hashed)
    assert not verify_password("wrong", hashed)


def test_malformed_password_hash_is_rejected():
    assert not verify_password(
        "correct horse battery staple",
        "not-a-valid-password-hash",
    )


def test_jwt_round_trip():
    token = create_access_token("42")

    assert decode_access_token(token) == "42"


def test_jwt_without_subject_is_rejected():
    token = jwt.encode(
        {
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_jwt_with_invalid_subject_is_rejected():
    token = jwt.encode(
        {
            "sub": "not-a-user-id",
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_jwt_with_wrong_token_type_is_rejected():
    token = jwt.encode(
        {
            "sub": "42",
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_expired_jwt_is_rejected():
    token = jwt.encode(
        {
            "sub": "42",
            "type": "access",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_malformed_jwt_is_rejected():
    assert decode_access_token("not.a.jwt") is None