from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.file import Chunk, ChunkReplica


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


def verify_content_hash(
    data: bytes,
    expected_hash: str,
) -> bool:
    """Verify that data matches the expected SHA-256 content hash."""
    return sha256(data).hexdigest() == expected_hash


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