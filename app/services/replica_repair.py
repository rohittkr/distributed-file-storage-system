from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import redis_client
from app.models.file import Chunk, ChunkReplica, StorageNode
from app.storage.local import LocalStorageBackend


REPAIR_LOCK_TTL_SECONDS = 30


def get_healthy_chunk_replicas(
    db: Session,
    chunk_id: int,
) -> list[ChunkReplica]:
    """Return replicas that belong to healthy storage nodes."""
    statement = (
        select(ChunkReplica)
        .join(
            StorageNode,
            ChunkReplica.storage_node_id == StorageNode.id,
        )
        .where(
            ChunkReplica.chunk_id == chunk_id,
            ChunkReplica.status == "healthy",
            StorageNode.status == "healthy",
        )
        .order_by(ChunkReplica.id)
    )

    return list(db.scalars(statement).all())


def _get_storage_for_node(
    node: StorageNode,
) -> LocalStorageBackend:
    """Return node-isolated local storage for a storage node."""
    return LocalStorageBackend(
        settings.local_storage_root,
        node_id=node.node_id,
    )


def _read_and_validate_replica(
    db: Session,
    replica: ChunkReplica,
    expected_checksum: str,
) -> bytes | None:
    """
    Read replica data and verify its checksum.

    Returns the valid data or None when the node or physical replica
    cannot be safely used as a repair source.
    """
    storage_node = db.get(
        StorageNode,
        replica.storage_node_id,
    )

    if storage_node is None:
        return None

    if storage_node.status != "healthy":
        return None

    storage = _get_storage_for_node(storage_node)

    try:
        data = storage.get(replica.storage_key)
    except FileNotFoundError:
        return None

    if sha256(data).hexdigest() != expected_checksum:
        return None

    return data


def _write_and_verify_replica(
    storage: LocalStorageBackend,
    storage_key: str,
    data: bytes,
    expected_checksum: str,
) -> bool:
    """Write replica data and verify it by reading it back."""
    storage.put(
        storage_key,
        data,
    )

    try:
        repaired_data = storage.get(
            storage_key,
        )
    except FileNotFoundError:
        return False

    return sha256(repaired_data).hexdigest() == expected_checksum


def repair_chunk_replica(
    db: Session,
    chunk_id: int,
) -> bool:
    """
    Repair one under-replicated chunk onto a healthy storage node.

    A per-chunk Redis lock prevents concurrent repair workers from
    performing duplicate repair work.

    Returns True when a new replica is created or an unhealthy replica
    is successfully restored. Returns False when no repair is necessary,
    another worker owns the repair lock, or no safe repair can currently
    be performed.
    """
    lock_key = f"dfs:replica-repair:lock:{chunk_id}"
    owner_token = token_urlsafe(32)

    lock_acquired = redis_client.set_if_not_exists(
        lock_key,
        owner_token,
        REPAIR_LOCK_TTL_SECONDS,
    )

    if not lock_acquired:
        return False

    try:
        chunk = db.get(Chunk, chunk_id)

        if chunk is None:
            return False

        healthy_replicas = get_healthy_chunk_replicas(
            db,
            chunk.id,
        )

        if len(healthy_replicas) >= settings.replication_factor:
            return False

        source_replica: ChunkReplica | None = None
        source_data: bytes | None = None

        for replica in healthy_replicas:
            candidate_data = _read_and_validate_replica(
                db,
                replica,
                chunk.checksum,
            )

            if candidate_data is None:
                continue

            source_replica = replica
            source_data = candidate_data
            break

        if source_replica is None or source_data is None:
            return False

        existing_healthy_node_ids = {
            replica.storage_node_id
            for replica in healthy_replicas
        }

        existing_replica_statement = (
            select(ChunkReplica)
            .join(
                StorageNode,
                ChunkReplica.storage_node_id == StorageNode.id,
            )
            .where(
                ChunkReplica.chunk_id == chunk.id,
                ChunkReplica.status != "healthy",
                StorageNode.status == "healthy",
            )
            .order_by(ChunkReplica.id)
        )

        existing_replica = db.scalars(
            existing_replica_statement
        ).first()

        if existing_replica is not None:
            destination_node = db.get(
                StorageNode,
                existing_replica.storage_node_id,
            )

            if destination_node is None:
                return False

        else:
            destination_statement = (
                select(StorageNode)
                .where(
                    StorageNode.status == "healthy",
                    ~StorageNode.id.in_(
                        existing_healthy_node_ids
                    ),
                )
                .order_by(
                    StorageNode.used_bytes.asc(),
                    StorageNode.id.asc(),
                )
            )

            destination_node = db.scalars(
                destination_statement
            ).first()

            if destination_node is None:
                return False

        destination_storage = _get_storage_for_node(
            destination_node,
        )

        destination_storage_key = (
            existing_replica.storage_key
            if existing_replica is not None
            else source_replica.storage_key
        )

        destination_path_exists = False

        try:
            existing_destination_data = destination_storage.get(
                destination_storage_key,
            )
            destination_path_exists = True
        except FileNotFoundError:
            existing_destination_data = None

        if existing_replica is not None:
            if (
                existing_destination_data is not None
                and sha256(existing_destination_data).hexdigest()
                == chunk.checksum
            ):
                existing_replica.status = "healthy"
                existing_replica.checksum = chunk.checksum

                db.commit()

                return True

            if not _write_and_verify_replica(
                destination_storage,
                destination_storage_key,
                source_data,
                chunk.checksum,
            ):
                return False

            existing_replica.status = "healthy"
            existing_replica.checksum = chunk.checksum

            db.commit()

            return True

        if (
            destination_node.capacity_bytes > 0
            and (
                destination_node.used_bytes + len(source_data)
                > destination_node.capacity_bytes
            )
        ):
            return False

        repaired_storage_key = source_replica.storage_key

        if not _write_and_verify_replica(
            destination_storage,
            repaired_storage_key,
            source_data,
            chunk.checksum,
        ):
            try:
                destination_storage.delete(
                    repaired_storage_key,
                )
            except OSError:
                pass

            return False

        repaired_replica = ChunkReplica(
            chunk_id=chunk.id,
            storage_node_id=destination_node.id,
            storage_key=repaired_storage_key,
            status="healthy",
            checksum=chunk.checksum,
        )

        db.add(repaired_replica)

        destination_node.used_bytes += len(source_data)

        db.commit()

        return True

    finally:
        redis_client.release_if_owner(
            lock_key,
            owner_token,
        )