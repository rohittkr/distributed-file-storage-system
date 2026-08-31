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
    node_id: str | None = None,
    endpoint: str = "http://storage-1:8080",
    capacity_bytes: int = 1_000_000,
):
    if node_id is None:
        node_id = f"node-{uuid4().hex}"

    return client.post(
        "/api/v1/storage/nodes",
        headers=auth_headers(token),
        json={
            "node_id": node_id,
            "endpoint": endpoint,
            "capacity_bytes": capacity_bytes,
        },
    )


def test_create_storage_node():
    _, token = create_user_and_login()

    node_id = f"node-{uuid4().hex}"

    response = create_storage_node(
        token,
        node_id=node_id,
    )

    assert response.status_code == 201

    body = response.json()

    assert body["node_id"] == node_id
    assert body["endpoint"] == "http://storage-1:8080"
    assert body["status"] == "healthy"
    assert body["capacity_bytes"] == 1_000_000
    assert body["used_bytes"] == 0
    assert body["last_heartbeat"] is not None
    assert "id" in body


def test_duplicate_storage_node_id_returns_conflict():
    _, token = create_user_and_login()

    node_id = f"duplicate-node-{uuid4().hex}"

    first_response = create_storage_node(
        token,
        node_id=node_id,
    )

    assert first_response.status_code == 201

    second_response = create_storage_node(
        token,
        node_id=node_id,
    )

    assert second_response.status_code == 409
    assert second_response.json() == {
        "detail": "A storage node with this node_id already exists."
    }


def test_list_storage_nodes():
    _, token = create_user_and_login()

    first_node_id = f"node-one-{uuid4().hex}"
    second_node_id = f"node-two-{uuid4().hex}"

    first_response = create_storage_node(
        token,
        node_id=first_node_id,
        endpoint="http://storage-1:8080",
    )
    assert first_response.status_code == 201

    second_response = create_storage_node(
        token,
        node_id=second_node_id,
        endpoint="http://storage-2:8080",
    )
    assert second_response.status_code == 201

    response = client.get(
        "/api/v1/storage/nodes",
        headers=auth_headers(token),
    )

    assert response.status_code == 200

    nodes = response.json()

    node_ids = {node["node_id"] for node in nodes}

    assert first_node_id in node_ids
    assert second_node_id in node_ids


def test_get_storage_node():
    _, token = create_user_and_login()

    node_id = f"node-{uuid4().hex}"

    create_response = create_storage_node(
        token,
        node_id=node_id,
    )

    assert create_response.status_code == 201

    created_node = create_response.json()

    response = client.get(
        f"/api/v1/storage/nodes/{node_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == created_node["id"]
    assert body["node_id"] == node_id
    assert body["endpoint"] == "http://storage-1:8080"
    assert body["status"] == "healthy"
    assert body["capacity_bytes"] == 1_000_000
    assert body["used_bytes"] == 0


def test_get_nonexistent_storage_node_returns_404():
    _, token = create_user_and_login()

    node_id = f"missing-{uuid4().hex}"

    response = client.get(
        f"/api/v1/storage/nodes/{node_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Storage node not found."
    }


def test_storage_node_heartbeat_updates_node():
    _, token = create_user_and_login()

    node_id = f"heartbeat-node-{uuid4().hex}"

    create_response = create_storage_node(
        token,
        node_id=node_id,
    )

    assert create_response.status_code == 201

    before = create_response.json()["last_heartbeat"]

    response = client.post(
        f"/api/v1/storage/nodes/{node_id}/heartbeat",
        headers=auth_headers(token),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == create_response.json()["id"]
    assert body["node_id"] == node_id
    assert body["status"] == "healthy"
    assert body["last_heartbeat"] is not None
    assert body["last_heartbeat"] >= before


def test_storage_node_fields_are_returned_correctly():
    _, token = create_user_and_login()

    node_id = f"fields-node-{uuid4().hex}"

    response = create_storage_node(
        token,
        node_id=node_id,
        endpoint="http://storage-custom:9000",
        capacity_bytes=5_000_000,
    )

    assert response.status_code == 201

    body = response.json()

    expected_fields = {
        "id",
        "node_id",
        "endpoint",
        "status",
        "capacity_bytes",
        "used_bytes",
        "last_heartbeat",
    }

    assert set(body.keys()) == expected_fields

    assert body["node_id"] == node_id
    assert body["endpoint"] == "http://storage-custom:9000"
    assert body["status"] == "healthy"
    assert body["capacity_bytes"] == 5_000_000
    assert body["used_bytes"] == 0
    assert body["last_heartbeat"] is not None
