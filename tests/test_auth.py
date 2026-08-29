from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User


client = TestClient(app)


def unique_email() -> str:
    return f"test-{uuid4().hex}@example.com"


def delete_user(email: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))

        if user is not None:
            db.delete(user)
            db.commit()


def test_register_creates_postgresql_user():
    email = unique_email()

    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert response.status_code == 201

        body = response.json()

        assert body["email"] == email
        assert body["id"] > 0
        assert body["is_active"] is True
        assert "password" not in body
        assert "password_hash" not in body

        with SessionLocal() as db:
            user = db.scalar(
                select(User).where(User.email == email)
            )

            assert user is not None
            assert user.password_hash != "correct horse battery staple"
            assert user.password_hash.startswith("$argon2")
    finally:
        delete_user(email)


def test_duplicate_email_returns_conflict():
    email = unique_email()

    try:
        first_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert first_response.status_code == 201

        second_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "another secure password",
            },
        )

        assert second_response.status_code == 409
        assert second_response.json() == {
            "detail": "An account with this email already exists."
        }
    finally:
        delete_user(email)


def test_invalid_registration_password_is_rejected():
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": unique_email(),
            "password": "short",
        },
    )

    assert response.status_code == 422


def test_invalid_registration_email_is_rejected():
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 422


def test_login_returns_access_token():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str)
        assert len(body["access_token"]) > 20
    finally:
        delete_user(email)


def test_invalid_password_is_rejected():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "wrong password",
            },
        )

        assert response.status_code == 401
        assert response.json() == {
            "detail": "Invalid email or password."
        }
    finally:
        delete_user(email)


def test_unknown_email_is_rejected_without_user_enumeration():
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": unique_email(),
            "password": "wrong password",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Invalid email or password."
    }


def test_me_requires_authentication():
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Not authenticated"
    }


def test_me_returns_authenticated_user():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert login_response.status_code == 200

        token = login_response.json()["access_token"]

        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        body = response.json()

        assert body["email"] == email
        assert "password" not in body
        assert "password_hash" not in body
    finally:
        delete_user(email)


def test_malformed_token_is_rejected():
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Invalid or expired authentication credentials."
    }


def test_expired_token_is_rejected():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        user_id = register_response.json()["id"]

        expired_token = jwt.encode(
            {
                "sub": str(user_id),
                "type": "access",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

        assert response.status_code == 401
        assert response.json() == {
            "detail": "Invalid or expired authentication credentials."
        }
    finally:
        delete_user(email)


def test_inactive_user_cannot_login():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        user_id = register_response.json()["id"]

        with SessionLocal() as db:
            user = db.get(User, user_id)

            assert user is not None

            user.is_active = False
            db.commit()

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert login_response.status_code == 401
        assert login_response.json() == {
            "detail": "Invalid email or password."
        }

    finally:
        delete_user(email)


def test_inactive_user_cannot_access_me():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        user_id = register_response.json()["id"]

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )

        assert login_response.status_code == 200

        token = login_response.json()["access_token"]

        with SessionLocal() as db:
            user = db.get(User, user_id)

            assert user is not None

            user.is_active = False
            db.commit()

        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 401

    finally:
        delete_user(email)


def test_email_is_case_insensitive():
    email = unique_email()

    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email.upper(),
                "password": "correct horse battery staple",
            },
        )

        assert register_response.status_code == 201

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": email.upper(),
                "password": "correct horse battery staple",
            },
        )

        assert login_response.status_code == 200
    finally:
        delete_user(email)