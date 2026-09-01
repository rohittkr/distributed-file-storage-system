from unittest.mock import MagicMock

from app.core import cache


def test_file_cache_key():
    assert cache.file_cache_key(42, 7) == "file:7:42"


def test_get_cached_file():
    cache.redis_client.get = MagicMock(
        return_value={
            "id": 42,
            "name": "test.txt",
        }
    )

    result = cache.get_cached_file(42, 7)

    assert result == {
        "id": 42,
        "name": "test.txt",
    }

    cache.redis_client.get.assert_called_once_with(
        "file:7:42"
    )


def test_get_cached_file_returns_none_on_cache_miss():
    cache.redis_client.get = MagicMock(
        return_value=None
    )

    result = cache.get_cached_file(42, 7)

    assert result is None

    cache.redis_client.get.assert_called_once_with(
        "file:7:42"
    )


def test_cache_file():
    cache.redis_client.set = MagicMock(
        return_value=True
    )

    value = {
        "id": 42,
        "name": "test.txt",
    }

    result = cache.cache_file(
        42,
        7,
        value,
    )

    assert result is True

    cache.redis_client.set.assert_called_once_with(
        "file:7:42",
        value,
        ttl_seconds=300,
    )


def test_invalidate_file_cache():
    cache.redis_client.delete = MagicMock(
        return_value=1
    )

    result = cache.invalidate_file_cache(42, 7)

    assert result == 1

    cache.redis_client.delete.assert_called_once_with(
        "file:7:42"
    )