from hashlib import sha256
from uuid import uuid4

from app.db.session import SessionLocal
from app.models.file import (
    ContentObject,
    ContentObjectReplica,
    StorageNode,
)


def unique_content() -> bytes:
    """Return unique test content for database isolation."""
    return f"replica-test-{uuid4().hex}".encode()


def create_content_object(db, content: bytes) -> ContentObject:
    content_hash = sha256(content).hexdigest()

    content_object = ContentObject(
        content_hash=content_hash,
        size_bytes=len(content),
        reference_count=1,
    )

    db.add(content_object)
    db.commit()
    db.refresh(content_object)

    return content_object


def create_storage_node(db) -> StorageNode:
    node = StorageNode(
        node_id=f"replica-test-node-{uuid4().hex}",
        endpoint="http://localhost:9000",
        status="healthy",
        capacity_bytes=1_000_000,
        used_bytes=0,
    )

    db.add(node)
    db.commit()
    db.refresh(node)

    return node


def test_create_content_object_replica():
    content = unique_content()

    with SessionLocal() as db:
        content_object = create_content_object(db, content)
        storage_node = create_storage_node(db)

        replica = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=storage_node.id,
            storage_key=f"content/{content_object.content_hash}",
            status="healthy",
            checksum=content_object.content_hash,
        )

        db.add(replica)
        db.commit()
        db.refresh(replica)

        assert replica.id is not None
        assert replica.content_object_id == content_object.id
        assert replica.storage_node_id == storage_node.id
        assert replica.status == "healthy"
        assert replica.checksum == content_object.content_hash


def test_content_object_replica_relationships():
    content = unique_content()

    with SessionLocal() as db:
        content_object = create_content_object(db, content)
        storage_node = create_storage_node(db)

        replica = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=storage_node.id,
            storage_key=f"content/{content_object.content_hash}",
            status="healthy",
            checksum=content_object.content_hash,
        )

        db.add(replica)
        db.commit()
        db.refresh(replica)

        assert replica.content_object.id == content_object.id
        assert replica.storage_node.id == storage_node.id
        assert replica in content_object.replicas
        assert replica in storage_node.content_object_replicas


def test_same_content_object_cannot_have_two_replicas_on_same_node():
    content = unique_content()

    with SessionLocal() as db:
        content_object = create_content_object(db, content)
        storage_node = create_storage_node(db)

        first = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=storage_node.id,
            storage_key=f"content/{content_object.content_hash}",
            status="healthy",
            checksum=content_object.content_hash,
        )

        db.add(first)
        db.commit()

        duplicate = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=storage_node.id,
            storage_key=f"content/{content_object.content_hash}-duplicate",
            status="healthy",
            checksum=content_object.content_hash,
        )

        db.add(duplicate)

        try:
            db.commit()
        except Exception:
            db.rollback()
            duplicate_failed = True
        else:
            duplicate_failed = False

        assert duplicate_failed


def test_same_content_object_can_have_replicas_on_different_nodes():
    content = unique_content()

    with SessionLocal() as db:
        content_object = create_content_object(db, content)

        first_node = create_storage_node(db)
        second_node = create_storage_node(db)

        first_replica = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=first_node.id,
            storage_key=f"content/{content_object.content_hash}/node-1",
            status="healthy",
            checksum=content_object.content_hash,
        )

        second_replica = ContentObjectReplica(
            content_object_id=content_object.id,
            storage_node_id=second_node.id,
            storage_key=f"content/{content_object.content_hash}/node-2",
            status="healthy",
            checksum=content_object.content_hash,
        )

        db.add_all([first_replica, second_replica])
        db.commit()

        db.refresh(content_object)

        assert len(content_object.replicas) == 2
        assert first_replica.storage_node_id != second_replica.storage_node_id