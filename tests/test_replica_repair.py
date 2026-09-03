from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from sqlalchemy import select

from app.core.config import settings
from app.core.redis import redis_client
from app.db.session import SessionLocal
from app.models.file import (
    Chunk,
    ChunkReplica,
    File,
    FileVersion,
    StorageNode,
)
from app.services.replica_repair import (
    get_healthy_chunk_replicas,
    repair_chunk_replica,
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
                node_id=f"repair-node-{index}-{uuid4().hex}",
                endpoint=f"local://repair-node-{index}",
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
            name=f"repair-{uuid4().hex}.bin",
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


def get_replica(
    db,
    chunk_id: int,
    node_id: int,
) -> ChunkReplica | None:
    return db.scalar(
        select(ChunkReplica).where(
            ChunkReplica.chunk_id == chunk_id,
            ChunkReplica.storage_node_id == node_id,
        )
    )


def get_storage_path(
    db,
    node_id: int,
    storage_key: str,
    root: Path,
) -> Path:
    node = db.get(
        StorageNode,
        node_id,
    )

    assert node is not None

    return (
        root
        / "nodes"
        / node.node_id
        / storage_key
    )


def write_source_data(
    db,
    replica: ChunkReplica,
    content: bytes,
    root: Path,
) -> Path:
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

    return (
        root
        / "nodes"
        / storage_node.node_id
        / replica.storage_key
    )


def test_get_healthy_chunk_replicas_excludes_failed_nodes():
    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"healthy replica lookup"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        second_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key="users/test/replica-1",
            status="healthy",
            checksum=sha256(content).hexdigest(),
        )

        db.add(second_replica)

        failed_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert failed_node is not None

        failed_node.status = "failed"

        db.commit()

        replicas = get_healthy_chunk_replicas(
            db,
            chunk_id,
        )

        assert len(replicas) == 1
        assert replicas[0].storage_node_id == node_ids[0]


def test_repair_chunk_replica_creates_replica_on_healthy_node(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"repairable content"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        source_storage = write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        assert source_storage.exists()

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is True

        replicas = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas) == 2

        destination_replica = next(
            replica
            for replica in replicas
            if replica.storage_node_id != node_ids[0]
        )

        assert destination_replica.checksum == sha256(
            content
        ).hexdigest()

        destination_node = db.get(
            StorageNode,
            destination_replica.storage_node_id,
        )

        assert destination_node is not None
        assert destination_node.status == "healthy"

        destination_storage = get_storage_path(
            db,
            destination_replica.storage_node_id,
            destination_replica.storage_key,
            tmp_path,
        )

        assert destination_storage.exists()
        assert destination_storage.read_bytes() == content


def test_repair_chunk_replica_is_idempotent(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"idempotent repair content"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        source_node = db.get(
            StorageNode,
            node_ids[0],
        )

        assert source_node is not None

        initial_source_used_bytes = source_node.used_bytes

        assert repair_chunk_replica(
            db,
            chunk_id,
        ) is True

        db.expire_all()

        replicas_after_first_repair = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas_after_first_repair) == 2

        destination_node = next(
            node
            for node in db.scalars(
                select(StorageNode).where(
                    StorageNode.id != node_ids[0],
                )
            ).all()
        )

        destination_used_bytes = destination_node.used_bytes

        assert repair_chunk_replica(
            db,
            chunk_id,
        ) is False

        db.expire_all()

        replicas_after_second_repair = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas_after_second_repair) == 2

        refreshed_source_node = db.get(
            StorageNode,
            node_ids[0],
        )

        refreshed_destination_node = db.get(
            StorageNode,
            destination_node.id,
        )

        assert refreshed_source_node is not None
        assert refreshed_destination_node is not None

        assert (
            refreshed_source_node.used_bytes
            == initial_source_used_bytes
        )

        assert (
            refreshed_destination_node.used_bytes
            == destination_used_bytes
        )


def test_repair_does_not_use_failed_destination_node(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(3)

    content = b"failed destination test"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        failed_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert failed_node is not None

        failed_node.status = "failed"

        db.commit()

        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is True

        replicas = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas) == 2

        destination_replica = next(
            replica
            for replica in replicas
            if replica.storage_node_id != node_ids[0]
        )

        assert destination_replica.storage_node_id != node_ids[0]
        assert destination_replica.storage_node_id != node_ids[1]


def test_repair_restores_existing_unhealthy_replica(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"restore existing replica"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        destination_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert destination_node is not None

        destination_node.used_bytes = 0

        destination_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="failed",
            checksum=source_replica.checksum,
        )

        db.add(destination_replica)
        db.commit()

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is True

        db.refresh(destination_replica)
        db.refresh(destination_node)

        assert destination_replica.status == "healthy"
        assert destination_replica.checksum == sha256(
            content
        ).hexdigest()

        destination_storage = get_storage_path(
            db,
            node_ids[1],
            destination_replica.storage_key,
            tmp_path,
        )

        assert destination_storage.exists()
        assert destination_storage.read_bytes() == content


def test_repair_restores_corrupted_existing_replica(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"correct replica content"
    corrupted_content = b"corrupted replica content"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        destination_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert destination_node is not None

        destination_node.used_bytes = 0

        destination_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="failed",
            checksum=source_replica.checksum,
        )

        db.add(destination_replica)
        db.commit()

        destination_storage = LocalStorageBackend(
            str(tmp_path),
            node_id=destination_node.node_id,
        )

        destination_storage.put(
            destination_replica.storage_key,
            corrupted_content,
        )

        before_used_bytes = destination_node.used_bytes

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is True

        db.refresh(destination_replica)
        db.refresh(destination_node)

        assert destination_replica.status == "healthy"
        assert destination_replica.checksum == sha256(
            content
        ).hexdigest()

        restored_data = destination_storage.get(
            destination_replica.storage_key,
        )

        assert restored_data == content

        assert (
            destination_node.used_bytes
            == before_used_bytes
        )


def test_repair_returns_false_when_all_sources_are_invalid(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"source content"
    corrupted_content = b"corrupted source"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        source_node = db.get(
            StorageNode,
            node_ids[0],
        )

        assert source_node is not None

        source_storage = LocalStorageBackend(
            str(tmp_path),
            node_id=source_node.node_id,
        )

        source_storage.put(
            source_replica.storage_key,
            corrupted_content,
        )

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is False

        replicas = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas) == 1

        destination_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert destination_node is not None
        assert destination_node.used_bytes == 0


def test_repair_does_not_double_count_existing_destination_data(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"existing destination accounting"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

        destination_node = db.get(
            StorageNode,
            node_ids[1],
        )

        assert destination_node is not None

        destination_replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node_ids[1],
            storage_key=source_replica.storage_key,
            status="failed",
            checksum=source_replica.checksum,
        )

        db.add(destination_replica)

        destination_node.used_bytes = len(content)

        db.commit()

        repaired = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired is True

        db.refresh(destination_node)

        assert destination_node.used_bytes == len(content)

        repaired_again = repair_chunk_replica(
            db,
            chunk_id,
        )

        assert repaired_again is False

        db.refresh(destination_node)

        assert destination_node.used_bytes == len(content)


def test_repair_lock_prevents_concurrent_repair(
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

    user_id = create_test_user()
    node_ids = create_test_nodes(2)

    content = b"repair concurrent lock test"

    chunk_id = create_test_chunk(
        user_id,
        node_ids[0],
        content,
    )

    with SessionLocal() as db:
        source_replica = get_replica(
            db,
            chunk_id,
            node_ids[0],
        )

        assert source_replica is not None

        write_source_data(
            db,
            source_replica,
            content,
            tmp_path,
        )

    barrier = Barrier(2)

    def repair_worker() -> bool:
        barrier.wait(timeout=5)

        with SessionLocal() as db:
            return repair_chunk_replica(
                db,
                chunk_id,
            )

    with ThreadPoolExecutor(
        max_workers=2,
    ) as executor:
        futures = [
            executor.submit(repair_worker)
            for _ in range(2)
        ]

        results = [
            future.result()
            for future in futures
        ]

    assert sorted(results) == [False, True]

    with SessionLocal() as db:
        replicas = list(
            db.scalars(
                select(ChunkReplica).where(
                    ChunkReplica.chunk_id == chunk_id,
                )
            ).all()
        )

        assert len(replicas) == 2

        destination_nodes = [
            replica.storage_node_id
            for replica in replicas
        ]

        assert len(set(destination_nodes)) == 2