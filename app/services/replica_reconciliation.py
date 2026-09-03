from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.file import Chunk, ChunkReplica, StorageNode
from app.services.replica_repair import repair_chunk_replica


def get_under_replicated_chunk_ids(
    db: Session,
) -> list[int]:
    """
    Return chunk IDs whose number of healthy replicas is below
    the configured replication factor.
    """
    healthy_replica_count = func.count(
        ChunkReplica.id,
    ).filter(
        ChunkReplica.status == "healthy",
        StorageNode.status == "healthy",
    )

    statement = (
        select(Chunk.id)
        .outerjoin(
            ChunkReplica,
            ChunkReplica.chunk_id == Chunk.id,
        )
        .outerjoin(
            StorageNode,
            StorageNode.id == ChunkReplica.storage_node_id,
        )
        .group_by(Chunk.id)
        .having(
            healthy_replica_count
            < settings.replication_factor
        )
        .order_by(Chunk.id)
    )

    return list(
        db.scalars(statement).all()
    )


def reconcile_under_replicated_chunks(
    db: Session,
) -> dict[str, int]:
    """
    Detect and repair chunks that have fewer healthy replicas than
    the configured replication factor.
    """
    chunk_ids = get_under_replicated_chunk_ids(
        db,
    )

    repaired = 0
    skipped = 0

    for chunk_id in chunk_ids:
        if repair_chunk_replica(
            db,
            chunk_id,
        ):
            repaired += 1
        else:
            skipped += 1

    return {
        "checked": len(chunk_ids),
        "repaired": repaired,
        "skipped": skipped,
    }