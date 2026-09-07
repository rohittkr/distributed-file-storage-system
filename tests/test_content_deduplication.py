from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.file import (
    Chunk,
    ChunkReplica,
    ContentObject,
    File,
    FileVersion,
    StorageNode,
)
from app.models.user import User
from app.services.content_deduplication import (
    acquire_content_object,
    find_existing_chunk_by_content_hash,
    get_healthy_replicas_for_content,
    verify_content_hash,
)


def create_test_user(db) -> User:
    """Create a unique user for an isolated test fixture."""
    user = User(
        email=f"dedup-{uuid4().hex}@example.com",
        password_hash="test-password-hash",
        quota_bytes=1024 * 1024,
        used_bytes=0,
    )
    db.add(user)
    db.flush()
    return user


def create_test_chunk(
    db,
    *,
    content: bytes,
) -> int:
    user = create_test_user(db)

    file = File(
        owner_id=user.id,
        name=f"dedup-{uuid4().hex}.bin",
        mime_type="application/octet-stream",
        size_bytes=len(content),
    )
    db.add(file)
    db.flush()

    version = FileVersion(
        file_id=file.id,
        version_number=1,
        size_bytes=len(content),
        checksum=sha256(content).hexdigest(),
    )
    db.add(version)
    db.flush()

    chunk = Chunk(
        version_id=version.id,
        chunk_number=0,
        size_bytes=len(content),
        checksum=sha256(content).hexdigest(),
        content_hash=sha256(content).hexdigest(),
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)

    return chunk.id


def test_find_existing_chunk_by_content_hash():
    content = b"deduplicated content"

    with SessionLocal() as db:
        chunk_id = create_test_chunk(
            db,
            content=content,
        )

        result = find_existing_chunk_by_content_hash(
            db,
            sha256(content).hexdigest(),
            len(content),
        )

        assert result is not None
        assert result.content_hash == sha256(content).hexdigest()
        assert result.size_bytes == len(content)

        matching_chunks = list(
            db.scalars(
                select(Chunk).where(
                    Chunk.content_hash
                    == sha256(content).hexdigest(),
                    Chunk.size_bytes == len(content),
                )
            ).all()
        )

        assert any(
            chunk.id == chunk_id
            for chunk in matching_chunks
        )


def test_find_existing_chunk_requires_matching_size():
    content = b"deduplicated content"

    with SessionLocal() as db:
        create_test_chunk(
            db,
            content=content,
        )

        result = find_existing_chunk_by_content_hash(
            db,
            sha256(content).hexdigest(),
            len(content) + 1,
        )

        assert result is None


def test_verify_content_hash_accepts_matching_content():
    content = b"verified content"

    assert verify_content_hash(
        content,
        sha256(content).hexdigest(),
    )


def test_verify_content_hash_rejects_modified_content():
    content = b"verified content"

    assert not verify_content_hash(
        b"modified content",
        sha256(content).hexdigest(),
    )


def test_get_healthy_replicas_for_content():
    content = b"replicated dedup content"

    with SessionLocal() as db:
        chunk_id = create_test_chunk(
            db,
            content=content,
        )

        node = StorageNode(
            node_id=f"dedup-node-{uuid4().hex}",
            endpoint="http://localhost:9000",
            status="healthy",
            capacity_bytes=1024 * 1024,
            used_bytes=len(content),
        )
        db.add(node)
        db.flush()

        replica = ChunkReplica(
            chunk_id=chunk_id,
            storage_node_id=node.id,
            storage_key=f"dedup/{uuid4().hex}",
            status="healthy",
            checksum=sha256(content).hexdigest(),
        )
        db.add(replica)
        db.commit()

        chunk = db.get(Chunk, chunk_id)

        assert chunk is not None

        result = get_healthy_replicas_for_content(
            db,
            chunk,
        )

        assert len(result) == 1
        assert result[0].id == replica.id


def test_concurrent_identical_content_creates_single_content_object():
    content = f"concurrent-dedup-{uuid4().hex}".encode()
    content_hash = sha256(content).hexdigest()
    size_bytes = len(content)

    def acquire():
        with SessionLocal() as db:
            result = acquire_content_object(
                db,
                content,
            )
            db.commit()

            return result[0].id, result[1]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(acquire)
            for _ in range(2)
        ]

        results = [
            future.result()
            for future in futures
        ]

    with SessionLocal() as db:
        objects = list(
            db.scalars(
                select(ContentObject).where(
                    ContentObject.content_hash == content_hash,
                    ContentObject.size_bytes == size_bytes,
                )
            ).all()
        )

        assert len(objects) == 1

        content_object = objects[0]

        assert content_object.reference_count == 2

        returned_ids = [
            object_id
            for object_id, _created in results
        ]

        assert returned_ids == [
            content_object.id,
            content_object.id,
        ]

        created_flags = [
            created
            for _object_id, created in results
        ]

        assert created_flags.count(True) == 1
        assert created_flags.count(False) == 1