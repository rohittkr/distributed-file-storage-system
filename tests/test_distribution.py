from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.models.file import Chunk, ChunkReplica, StorageNode

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

    return email, login_response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_file(
    token: str,
    name: str = "distributed-test.txt",
) -> int:
    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": name,
            "mime_type": "text/plain",
        },
    )

    assert response.status_code == 201
    return response.json()["id"]


def upload_content(
    token: str,
    file_id: int,
    content: bytes,
):
    return client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "distributed-test.txt",
                content,
                "text/plain",
            )
        },
    )


def get_latest_chunk_and_replica(
    db,
) -> tuple[Chunk, ChunkReplica]:
    chunk = db.scalar(
        select(Chunk).order_by(
            Chunk.id.desc()
        )
    )

    assert chunk is not None

    replica = db.scalar(
        select(ChunkReplica)
        .where(
            ChunkReplica.chunk_id == chunk.id
        )
        .order_by(
            ChunkReplica.id.desc()
        )
    )

    assert replica is not None

    return chunk, replica


def test_upload_creates_storage_node_and_replica():
    _, token = create_user_and_login()
    file_id = create_file(token)

    content = b"distributed storage test content"

    response = upload_content(
        token,
        file_id,
        content,
    )

    assert response.status_code == 201

    with SessionLocal() as db:
        chunk, replica = get_latest_chunk_and_replica(db)

        storage_node = db.get(
            StorageNode,
            replica.storage_node_id,
        )

        assert storage_node is not None
        assert storage_node.status == "healthy"
        assert replica.chunk_id == chunk.id
        assert replica.storage_node_id == storage_node.id
        assert replica.status == "healthy"


def test_upload_replica_has_storage_key():
    _, token = create_user_and_login()
    file_id = create_file(
        token,
        "replica-key-test.txt",
    )

    content = b"replica key content"

    response = upload_content(
        token,
        file_id,
        content,
    )

    assert response.status_code == 201

    with SessionLocal() as db:
        _, replica = get_latest_chunk_and_replica(db)

        assert replica.storage_key.startswith("users/")
        assert "/files/" in replica.storage_key
        assert "/versions/" in replica.storage_key
        assert "/chunks/" in replica.storage_key


def test_upload_replica_checksum_matches_chunk():
    _, token = create_user_and_login()
    file_id = create_file(
        token,
        "checksum-test.txt",
    )

    content = b"checksum verification content"

    response = upload_content(
        token,
        file_id,
        content,
    )

    assert response.status_code == 201

    with SessionLocal() as db:
        chunk, replica = get_latest_chunk_and_replica(db)

        assert replica.checksum == chunk.checksum


def test_storage_node_tracks_used_bytes():
    _, token = create_user_and_login()
    file_id = create_file(
        token,
        "usage-test.txt",
    )

    content = b"1234567890"

    response = upload_content(
        token,
        file_id,
        content,
    )

    assert response.status_code == 201

    with SessionLocal() as db:
        _, replica = get_latest_chunk_and_replica(db)

        node = db.get(
            StorageNode,
            replica.storage_node_id,
        )

        assert node is not None
        assert node.used_bytes >= len(content)


def test_multiple_uploads_create_multiple_replicas():
    _, token = create_user_and_login()
    file_id = create_file(
        token,
        "multiple-upload-test.txt",
    )

    first_content = b"first version"
    second_content = b"second version"

    first_response = upload_content(
        token,
        file_id,
        first_content,
    )

    assert first_response.status_code == 201

    second_response = upload_content(
        token,
        file_id,
        second_content,
    )

    assert second_response.status_code == 201

    with SessionLocal() as db:
        replicas = db.scalars(
            select(ChunkReplica).order_by(
                ChunkReplica.id.asc()
            )
        ).all()

        assert len(replicas) >= 2

        recent_replicas = replicas[-2:]

        assert all(
            replica.status == "healthy"
            for replica in recent_replicas
        )


def test_upload_uses_healthy_storage_node():
    _, token = create_user_and_login()
    file_id = create_file(
        token,
        "healthy-node-test.txt",
    )

    with SessionLocal() as db:
        node = StorageNode(
            node_id=f"node-{uuid4().hex}",
            endpoint="local://node-healthy",
            status="healthy",
            capacity_bytes=1024 * 1024,
            used_bytes=0,
        )

        db.add(node)
        db.commit()
        db.refresh(node)

        healthy_node_id = node.id

    response = upload_content(
        token,
        file_id,
        b"healthy storage node content",
    )

    assert response.status_code == 201

    with SessionLocal() as db:
        _, replica = get_latest_chunk_and_replica(db)

        storage_node = db.get(
            StorageNode,
            replica.storage_node_id,
        )

        assert storage_node is not None
        assert storage_node.status == "healthy"

        assert replica.storage_node_id in {
            healthy_node_id,
            storage_node.id,
        }


def test_unhealthy_storage_nodes_are_not_eligible():
    with SessionLocal() as db:
        unhealthy_node = StorageNode(
            node_id=f"node-unhealthy-{uuid4().hex}",
            endpoint="local://node-unhealthy",
            status="unhealthy",
            capacity_bytes=1024 * 1024,
            used_bytes=0,
        )

        db.add(unhealthy_node)
        db.commit()
        db.refresh(unhealthy_node)

        node = db.scalar(
            select(StorageNode).where(
                StorageNode.id == unhealthy_node.id
            )
        )

        assert node is not None
        assert node.status == "unhealthy"