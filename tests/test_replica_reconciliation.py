from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.file import (
    Chunk,
    ChunkReplica,
    File,
    FileVersion,
    StorageNode,
)
from app.services.replica_reconciliation import (
    get_under_replicated_chunk_ids,
    reconcile_under_replicated_chunks,
)
from app.storage.local import LocalStorageBackend


def create_test_user() -> int:
    from app.models.user import User

    email = f"{uuid4().hex}@example.com"

    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash="test-password-hash",
            quota_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        return user.id


def create_test_nodes(
    count: int = 3,
) -> list[int]:
    node_ids = []

    with SessionLocal() as db:
        for index in range(count):
            node = StorageNode(
                node_id=f"reconciliation-node-{index}-{uuid4().hex}",
                endpoint=f"local://reconciliation-node-{index}",
                status="healthy",
                capacity_bytes=1024 * 1024 * 1024,
                used_bytes=0,
            )
            db.add(node)
            db.flush()
            node_ids.append(node.id)

        db.commit()

    return node_ids


def create_test_chunk(
    user_id: int,
    node_id: int,
    content: bytes,
) -> int:
    checksum = sha256(content).hexdigest()

    with SessionLocal() as db:
        file = File(
            owner_id=user_id,
            name=f"reconciliation-{uuid4().hex}.bin",
            mime_type="application/octet-stream",
            size_bytes=len(content),
        )
        db.add(file)
        db.flush()

        version = FileVersion(
            file_id=file.id,
            version_number=1,
            size_bytes=len(content),
            checksum=checksum,
        )
        db.add(version)
        db.flush()

        chunk = Chunk(
            version_id=version.id,
            chunk_number=0,
            size_bytes=len(content),
            checksum=checksum,
            content_hash=checksum,
        )
        db.add(chunk)
        db.flush()

        storage_key = (
            f"users/{user_id}/files/{file.id}/"
            f"versions/1/chunks/0/replica-0"
        )

        replica = ChunkReplica(
            chunk_id=chunk.id,
            storage_node_id=node_id,
            storage_key=storage_key,
            status="healthy",
            checksum=checksum,
        )
        db.add(replica)

        storage_node = db.get(
            StorageNode,
            node_id,
        )

        assert storage_node is not None

        storage_node.used_bytes += len(content)

        db.commit()

        return chunk.id


def write_source_data(
    db,
    replica: ChunkReplica,
    content: bytes,
    root,
) -> None:
    storage_node = db.get(
        StorageNode,
        replica.storage_node_id,
    )

    assert storage_node is not None

    storage = LocalStorageBackend(
        str(root),
        node_id=storage_node.node_id,
    )

    storage.put(
        replica.storage_key,
        content,
    )


def test_get_under_replicated_chunk_ids_returns_chunk_below_factor(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        b"under replicated content",
    )

    with SessionLocal() as db:
        result = get_under_replicated_chunk_ids(db)

    assert chunk_id in result


def test_get_under_replicated_chunk_ids_excludes_fully_replicated_chunk(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"fully replicated content"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = db.scalar(
            select(ChunkReplica).where(
                ChunkReplica.chunk_id == chunk_id,
                ChunkReplica.storage_node_id == node_ids[0],
            )
        )

        assert source_replica is not None

        second_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert second_node is not None

        second_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="healthy",
            checksum=source_replica.checksum,
        )

        db.add(second_replica)
        second_node.used_bytes += len(content)

        db.commit()

        result = get_under_replicated_chunk_ids(db)

    assert chunk_id not in result


def test_get_under_replicated_chunk_ids_ignores_failed_replica(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        b"failed replica content",
    )

    with SessionLocal() as db:
        source_replica = db.scalar(
            select(ChunkReplica).where(
                ChunkReplica.chunk_id == chunk_id,
                ChunkReplica.storage_node_id == node_ids[0],
            )
        )

        assert source_replica is not None

        second_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="failed",
            checksum=source_replica.checksum,
        )

        db.add(second_replica)
        db.commit()

        result = get_under_replicated_chunk_ids(db)

    assert chunk_id in result


def test_reconcile_repairs_under_replicated_chunk(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    monkeypatch.setattr(
        settings,
        "local_storage_root",
        str(tmp_path),
    )

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"reconcile repair content"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = db.scalar(
            select(ChunkReplica).where(
                ChunkReplica.chunk_id == chunk_id,
                ChunkReplica.storage_node_id == node_ids[0],
            )
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        result = reconcile_under_replicated_chunks(
            db,
        )

        assert result["checked"] >= 1
        assert result["repaired"] >= 1

        replicas = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                    ChunkReplica.status == "healthy",
                )
            ).all()
        )

        assert len(replicas) == 2


def test_reconcile_does_nothing_when_fully_replicated(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        settings,
        "replication_factor",
        2,
    )

    monkeypatch.setattr(
        settings,
        "local_storage_root",
        str(tmp_path),
    )

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"already replicated"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = db.scalar(
            select(ChunkReplica).where(
                ChunkReplica.chunk_id == chunk_id,
                ChunkReplica.storage_node_id == node_ids[0],
            )
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        second_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert second_node is not None

        second_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="healthy",
            checksum=source_replica.checksum,
        )

        db.add(second_replica)

        second_node.used_bytes += len(content)

        db.commit()

        under_replicated = (
            get_under_replicated_chunk_ids(db)
        )

        assert chunk_id not in under_replicated

        result = reconcile_under_replicated_chunks(
            db,
        )

    assert result["checked"] >= 0
    assert result["repaired"] >= 0
    assert result["skipped"] >= 0