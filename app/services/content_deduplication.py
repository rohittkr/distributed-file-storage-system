from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.redis import redis_client
from app.models.file import (
    Chunk,
    ChunkReplica,
    ContentObject,
    ContentObjectReplica,
    StorageNode,
)


CONTENT_OBJECT_LOCK_TTL_SECONDS = 30


def calculate_content_hash(data: bytes) -> str:
    """Return the SHA-256 hash of content."""
    return sha256(data).hexdigest()


def verify_content_hash(
    data: bytes,
    expected_hash: str,
) -> bool:
    """Verify that data matches the expected SHA-256 content hash."""
    return calculate_content_hash(data) == expected_hash


def build_content_object_lock_key(
    content_hash: str,
    size_bytes: int,
) -> str:
    """Build the Redis lock key for a content object."""
    return (
        "content-object:lock:"
        f"{content_hash}:{size_bytes}"
    )


def find_content_object(
    db: Session,
    content_hash: str,
    size_bytes: int,
) -> ContentObject | None:
    """Return an existing content object for identical content."""
    statement = (
        select(ContentObject)
        .where(
            ContentObject.content_hash == content_hash,
            ContentObject.size_bytes == size_bytes,
        )
        .limit(1)
    )

    return db.scalar(statement)


def create_content_object(
    db: Session,
    content_hash: str,
    size_bytes: int,
) -> ContentObject:
    """Create a new content object with one logical reference."""
    content_object = ContentObject(
        content_hash=content_hash,
        size_bytes=size_bytes,
        reference_count=1,
    )

    db.add(content_object)
    db.flush()

    return content_object


def acquire_content_object(
    db: Session,
    data: bytes,
) -> tuple[ContentObject, bool]:
    """
    Find or create a content object for the supplied bytes.

    A Redis lock coordinates concurrent requests for identical
    content. PostgreSQL remains the source of truth through the
    unique content hash/size constraint.

    Returns:
        (content_object, created)
    """
    content_hash = calculate_content_hash(data)
    size_bytes = len(data)

    existing = find_content_object(
        db,
        content_hash,
        size_bytes,
    )

    if existing is not None:
        existing.reference_count += 1
        db.flush()
        return existing, False

    lock_key = build_content_object_lock_key(
        content_hash,
        size_bytes,
    )
    owner_token = token_urlsafe(32)

    acquired = redis_client.set_if_not_exists(
        lock_key,
        owner_token,
        CONTENT_OBJECT_LOCK_TTL_SECONDS,
    )

    if not acquired:
        raise RuntimeError(
            "Content object is currently being created by another request."
        )

    try:
        existing = find_content_object(
            db,
            content_hash,
            size_bytes,
        )

        if existing is not None:
            existing.reference_count += 1
            db.flush()
            return existing, False

        try:
            content_object = create_content_object(
                db,
                content_hash,
                size_bytes,
            )
        except IntegrityError:
            db.rollback()

            existing = find_content_object(
                db,
                content_hash,
                size_bytes,
            )

            if existing is None:
                raise

            existing.reference_count += 1
            db.flush()

            return existing, False

        return content_object, True

    finally:
        redis_client.release_if_owner(
            lock_key,
            owner_token,
        )


def get_content_object_replicas(
    db: Session,
    content_object_id: int,
    healthy_only: bool = False,
) -> list[ContentObjectReplica]:
    """Return replicas associated with a content object."""
    statement = (
        select(ContentObjectReplica)
        .where(
            ContentObjectReplica.content_object_id
            == content_object_id,
        )
        .order_by(ContentObjectReplica.id)
    )

    if healthy_only:
        statement = statement.where(
            ContentObjectReplica.status == "healthy",
        )

    return list(db.scalars(statement).all())


def create_content_object_replica(
    db: Session,
    content_object: ContentObject,
    storage_node: StorageNode,
    storage_key: str,
) -> ContentObjectReplica:
    """Create a physical replica record for a content object."""
    existing = db.scalar(
        select(ContentObjectReplica)
        .where(
            ContentObjectReplica.content_object_id
            == content_object.id,
            ContentObjectReplica.storage_node_id
            == storage_node.id,
        )
        .limit(1)
    )

    if existing is not None:
        return existing

    replica = ContentObjectReplica(
        content_object_id=content_object.id,
        storage_node_id=storage_node.id,
        storage_key=storage_key,
        status="healthy",
        checksum=content_object.content_hash,
    )

    db.add(replica)
    db.flush()

    return replica


def find_existing_chunk_by_content_hash(
    db: Session,
    content_hash: str,
    size_bytes: int,
) -> Chunk | None:
    """Return an existing chunk with identical verified content."""
    statement = (
        select(Chunk)
        .where(
            Chunk.content_hash == content_hash,
            Chunk.size_bytes == size_bytes,
        )
        .order_by(Chunk.id)
        .limit(1)
    )

    return db.scalar(statement)


def get_healthy_replicas_for_content(
    db: Session,
    source_chunk: Chunk,
) -> list[ChunkReplica]:
    """
    Return healthy replicas that can be reused as sources for
    identical content.
    """
    statement = (
        select(ChunkReplica)
        .where(
            ChunkReplica.chunk_id == source_chunk.id,
            ChunkReplica.status == "healthy",
        )
        .order_by(ChunkReplica.id)
    )

    return list(db.scalars(statement).all())