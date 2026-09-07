from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.file import ContentObject


def unique_content() -> bytes:
    """Return unique test content for database isolation."""
    return f"content-object-{uuid4().hex}".encode()


def test_create_content_object():
    content = unique_content()
    content_hash = sha256(content).hexdigest()

    with SessionLocal() as db:
        content_object = ContentObject(
            content_hash=content_hash,
            size_bytes=len(content),
            reference_count=1,
        )

        db.add(content_object)
        db.commit()
        db.refresh(content_object)

        assert content_object.id is not None
        assert content_object.content_hash == content_hash
        assert content_object.size_bytes == len(content)
        assert content_object.reference_count == 1


def test_content_object_hash_and_size_are_unique():
    content = unique_content()
    content_hash = sha256(content).hexdigest()

    with SessionLocal() as db:
        first = ContentObject(
            content_hash=content_hash,
            size_bytes=len(content),
            reference_count=1,
        )

        db.add(first)
        db.commit()

        duplicate = ContentObject(
            content_hash=content_hash,
            size_bytes=len(content),
            reference_count=1,
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


def test_same_hash_with_different_size_is_allowed():
    content = unique_content()
    content_hash = sha256(content).hexdigest()

    with SessionLocal() as db:
        first = ContentObject(
            content_hash=content_hash,
            size_bytes=100,
            reference_count=1,
        )

        second = ContentObject(
            content_hash=content_hash,
            size_bytes=200,
            reference_count=1,
        )

        db.add_all([first, second])
        db.commit()

        objects = list(
            db.scalars(
                select(ContentObject).where(
                    ContentObject.content_hash == content_hash,
                )
            ).all()
        )

        assert len(objects) == 2


def test_content_object_reference_count_can_be_updated():
    content = unique_content()
    content_hash = sha256(content).hexdigest()

    with SessionLocal() as db:
        content_object = ContentObject(
            content_hash=content_hash,
            size_bytes=len(content),
            reference_count=1,
        )

        db.add(content_object)
        db.commit()

        content_object.reference_count += 1
        db.commit()
        db.refresh(content_object)

        assert content_object.reference_count == 2