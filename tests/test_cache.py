from unittest.mock import MagicMock, patch

from redis.exceptions import RedisError

from app.core import cache


def test_file_cache_key():
    assert cache.file_cache_key(42, 7) == "file:7:42"


def test_get_cached_file():
    with patch.object(
        cache.redis_client,
        "get",
        return_value={
            "id": 42,
            "name": "test.txt",
        },
    ) as mock_get:
        result = cache.get_cached_file(42, 7)

        assert result == {
            "id": 42,
            "name": "test.txt",
        }

        mock_get.assert_called_once_with("file:7:42")


def test_get_cached_file_returns_none_on_cache_miss():
    with patch.object(
        cache.redis_client,
        "get",
        return_value=None,
    ) as mock_get:
        result = cache.get_cached_file(42, 7)

        assert result is None

        mock_get.assert_called_once_with("file:7:42")


def test_cache_file():
    value = {
        "id": 42,
        "name": "test.txt",
    }

    with patch.object(
        cache.redis_client,
        "set",
        return_value=True,
    ) as mock_set:
        result = cache.cache_file(
            42,
            7,
            value,
        )

        assert result is True

        mock_set.assert_called_once_with(
            "file:7:42",
            value,
            ttl_seconds=300,
        )


def test_invalidate_file_cache():
    with patch.object(
        cache.redis_client,
        "delete",
        return_value=1,
    ) as mock_delete:
        result = cache.invalidate_file_cache(42, 7)

        assert result == 1

        mock_delete.assert_called_once_with("file:7:42")


def test_get_cached_file_handles_redis_error():
    with patch.object(
        cache.redis_client,
        "get",
        side_effect=RedisError("Redis unavailable"),
    ):
        result = cache.get_cached_file(42, 7)

        assert result is None


def test_cache_file_handles_redis_error():
    with patch.object(
        cache.redis_client,
        "set",
        side_effect=RedisError("Redis unavailable"),
    ):
        result = cache.cache_file(
            42,
            7,
            {
                "id": 42,
                "name": "test.txt",
            },
        )

        assert result is False


def test_invalidate_file_cache_handles_redis_error():
    with patch.object(
        cache.redis_client,
        "delete",
        side_effect=RedisError("Redis unavailable"),
    ):
        result = cache.invalidate_file_cache(42, 7)

        assert result == 0