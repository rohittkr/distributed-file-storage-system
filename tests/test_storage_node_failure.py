from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def create_user_and_login() -> tuple[str, str]:
    email = f"{uuid4().hex}@example.com"
    password = "StrongPassword123!"

    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
        },
    )
    assert register_response.status_code == 201

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    return email, token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_storage_node(
    token: str,
    node_id: str,
) -> dict:
    response = client.post(
        "/api/v1/storage/nodes",
        headers=auth_headers(token),
        json={
            "node_id": node_id,
            "endpoint": f"http://{node_id}:8000",
            "capacity_bytes": 1024 * 1024 * 1024,
        },
    )

    assert response.status_code == 201
    return response.json()


def test_fail_storage_node_marks_node_failed():
    _, token = create_user_and_login()

    node_id = f"failure-test-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["node_id"] == node_id
    assert body["status"] == "failed"


def test_recover_storage_node_marks_node_healthy():
    _, token = create_user_and_login()

    node_id = f"recovery-test-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    fail_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert fail_response.status_code == 200
    assert fail_response.json()["status"] == "failed"

    recover_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/recover",
        headers=auth_headers(token),
    )

    assert recover_response.status_code == 200

    body = recover_response.json()

    assert body["node_id"] == node_id
    assert body["status"] == "healthy"
    assert body["last_heartbeat"] is not None


def test_fail_unknown_storage_node_returns_404():
    _, token = create_user_and_login()

    node_id = f"missing-{uuid4().hex}"

    response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Storage node not found."
    }


def test_recover_unknown_storage_node_returns_404():
    _, token = create_user_and_login()

    node_id = f"missing-{uuid4().hex}"

    response = client.post(
        f"/api/v1/storage/nodes/{node_id}/recover",
        headers=auth_headers(token),
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Storage node not found."
    }


def test_failing_already_failed_storage_node_returns_409():
    _, token = create_user_and_login()

    node_id = f"double-failure-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    first_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "failed"

    second_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert second_response.status_code == 409
    assert second_response.json() == {
        "detail": "Storage node is already failed."
    }


def test_recover_already_healthy_storage_node_returns_409():
    _, token = create_user_and_login()

    node_id = f"double-recovery-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    response = client.post(
        f"/api/v1/storage/nodes/{node_id}/recover",
        headers=auth_headers(token),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Storage node is already healthy."
    }


def test_failed_storage_node_is_reported_by_get_node():
    _, token = create_user_and_login()

    node_id = f"get-failed-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    fail_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    assert fail_response.status_code == 200

    response = client.get(
        f"/api/v1/storage/nodes/{node_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["node_id"] == node_id
    assert response.json()["status"] == "failed"


def test_recovered_storage_node_is_reported_healthy():
    _, token = create_user_and_login()

    node_id = f"get-recovered-{uuid4().hex}"

    create_storage_node(
        token,
        node_id,
    )

    client.post(
        f"/api/v1/storage/nodes/{node_id}/fail",
        headers=auth_headers(token),
    )

    recover_response = client.post(
        f"/api/v1/storage/nodes/{node_id}/recover",
        headers=auth_headers(token),
    )

    assert recover_response.status_code == 200

    response = client.get(
        f"/api/v1/storage/nodes/{node_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["node_id"] == node_id
    assert response.json()["status"] == "healthy"