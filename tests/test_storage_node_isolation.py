from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.file import Chunk, ChunkReplica, StorageNode


client = TestClient(app)


def create_user_and_login() -> str:
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

    return login_response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_file(token: str, name: str) -> int:
    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": name,
            "mime_type": "application/octet-stream",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_test_nodes() -> tuple[int, int, str, str]:
    node_a_id = f"node-a-{uuid4().hex}"
    node_b_id = f"node-b-{uuid4().hex}"

    with SessionLocal() as db:
        node_a = StorageNode(
            node_id=node_a_id,
            endpoint=f"local://{node_a_id}",
            status="healthy",
            capacity_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        )
        node_b = StorageNode(
            node_id=node_b_id,
            endpoint=f"local://{node_b_id}",
            status="healthy",
            capacity_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        )

        db.add_all([node_a, node_b])
        db.commit()

        db.refresh(node_a)
        db.refresh(node_b)

        return (
            node_a.id,
            node_b.id,
            node_a.node_id,
            node_b.node_id,
        )


def patch_replica_selection(
    monkeypatch,
    node_a_db_id: int,
    node_b_db_id: int,
):
    def select_test_nodes(
        db,
        limit=None,
        exclude_node_ids=None,
    ):
        nodes = [
            db.get(StorageNode, node_a_db_id),
            db.get(StorageNode, node_b_db_id),
        ]

        nodes = [
            node
            for node in nodes
            if node is not None
        ]

        if exclude_node_ids:
            nodes = [
                node
                for node in nodes
                if node.id not in exclude_node_ids
            ]

        if limit is not None:
            nodes = nodes[:limit]

        return nodes

    monkeypatch.setattr(
        "app.api.router.get_healthy_storage_nodes",
        select_test_nodes,
    )


def test_normal_upload_isolated_per_storage_node_and_all_downloads_work(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        settings,
        "local_storage_root",
        str(tmp_path),
    )
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    (
        node_a_db_id,
        node_b_db_id,
        node_a_id,
        node_b_id,
    ) = create_test_nodes()

    patch_replica_selection(
        monkeypatch,
        node_a_db_id,
        node_b_db_id,
    )

    token = create_user_and_login()
    file_id = create_file(
        token,
        "node-isolation.bin",
    )

    content = b"node-aware distributed storage content"

    upload_response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "node-isolation.bin",
                content,
                "application/octet-stream",
            )
        },
    )

    assert upload_response.status_code == 201

    with SessionLocal() as db:
        chunk = db.scalar(
            select(Chunk)
            .where(
                Chunk.version.has(
                    file_id=file_id
                )
            )
            .order_by(Chunk.id.desc())
            .limit(1)
        )

        assert chunk is not None

        replicas = list(
            db.scalars(
                select(ChunkReplica)
                .where(
                    ChunkReplica.chunk_id == chunk.id
                )
                .order_by(ChunkReplica.id)
            ).all()
        )

        assert len(replicas) == 2

        node_ids = []

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            assert storage_node is not None

            node_ids.append(
                storage_node.node_id
            )

            node_path = (
                tmp_path
                / "nodes"
                / storage_node.node_id
                / replica.storage_key
            )

            assert node_path.exists()
            assert node_path.read_bytes() == content
            assert (
                sha256(
                    node_path.read_bytes()
                ).hexdigest()
                == chunk.checksum
            )

            assert not (
                tmp_path
                / replica.storage_key
            ).exists()

        assert set(node_ids) == {
            node_a_id,
            node_b_id,
        }

        first_replica_node = db.get(
            StorageNode,
            replicas[0].storage_node_id,
        )

        assert first_replica_node is not None

        first_replica_path = (
            tmp_path
            / "nodes"
            / first_replica_node.node_id
            / replicas[0].storage_key
        )

    first_replica_path.unlink()

    download_response = client.get(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
    )

    assert download_response.status_code == 200
    assert download_response.content == content

    version_response = client.get(
        f"/api/v1/files/{file_id}/versions/1/content",
        headers=auth_headers(token),
    )

    assert version_response.status_code == 200
    assert version_response.content == content

    share_response = client.post(
        f"/api/v1/files/{file_id}/shares",
        headers=auth_headers(token),
        json={
            "permission": "viewer",
        },
    )

    assert share_response.status_code == 201

    share_token = share_response.json()["token"]

    shared_download_response = client.get(
        f"/api/v1/shares/{share_token}/content",
    )

    assert shared_download_response.status_code == 200
    assert shared_download_response.content == content


def test_resumable_upload_isolated_per_storage_node(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        settings,
        "local_storage_root",
        str(tmp_path),
    )
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    (
        node_a_db_id,
        node_b_db_id,
        node_a_id,
        node_b_id,
    ) = create_test_nodes()

    patch_replica_selection(
        monkeypatch,
        node_a_db_id,
        node_b_db_id,
    )

    token = create_user_and_login()

    content = b"resumable node-aware content"

    session_response = client.post(
        "/api/v1/uploads",
        headers=auth_headers(token),
        json={
            "filename": (
                "resumable-node-isolation.bin"
            ),
            "total_size_bytes": len(content),
            "mime_type": (
                "application/octet-stream"
            ),
        },
    )

    assert session_response.status_code == 201

    session_id = session_response.json()["id"]
    file_id = session_response.json()["file_id"]

    chunk_response = client.post(
        f"/api/v1/uploads/{session_id}/chunks/0",
        headers=auth_headers(token),
        files={
            "upload": (
                "chunk.bin",
                content,
                "application/octet-stream",
            )
        },
    )

    assert chunk_response.status_code == 201

    with SessionLocal() as db:
        chunk = db.scalar(
            select(Chunk)
            .where(
                Chunk.version.has(
                    file_id=file_id
                )
            )
            .order_by(Chunk.id.desc())
            .limit(1)
        )

        assert chunk is not None

        replicas = list(
            db.scalars(
                select(ChunkReplica)
                .where(
                    ChunkReplica.chunk_id == chunk.id
                )
                .order_by(ChunkReplica.id)
            ).all()
        )

        assert len(replicas) == 2

        node_ids = []

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            assert storage_node is not None

            node_ids.append(
                storage_node.node_id
            )

            node_path = (
                tmp_path
                / "nodes"
                / storage_node.node_id
                / replica.storage_key
            )

            assert node_path.exists()
            assert node_path.read_bytes() == content

            assert not (
                tmp_path
                / replica.storage_key
            ).exists()

        assert set(node_ids) == {
            node_a_id,
            node_b_id,
        }

    complete_response = client.post(
        f"/api/v1/uploads/{session_id}/complete",
        headers=auth_headers(token),
    )

    assert complete_response.status_code == 200

    download_response = client.get(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
    )

    assert download_response.status_code == 200
    assert download_response.content == content