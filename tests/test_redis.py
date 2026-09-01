import json
from unittest.mock import MagicMock

import pytest

from app.core.redis import RedisClient


@pytest.fixture
def mock_redis(monkeypatch):
    client = MagicMock()

    client.ping.return_value = True
    client.get.return_value = None
    client.set.return_value = True
    client.delete.return_value = 1

    from_url = MagicMock(return_value=client)

    monkeypatch.setattr(
        "app.core.redis.redis.Redis.from_url",
        from_url,
    )

    return client


def test_redis_client_uses_configured_url(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    assert redis_client._url == "redis://localhost:6379/0"


def test_ping(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    assert redis_client.ping() is True

    mock_redis.ping.assert_called_once_with()


def test_get_returns_none_for_missing_key(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    result = redis_client.get("missing-key")

    assert result is None

    mock_redis.get.assert_called_once_with("missing-key")


def test_get_returns_decoded_value(mock_redis):
    mock_redis.get.return_value = '{"name":"test.txt"}'

    redis_client = RedisClient("redis://localhost:6379/0")

    result = redis_client.get("file:1")

    assert result == {"name": "test.txt"}

    mock_redis.get.assert_called_once_with("file:1")


def test_set_stores_json_without_ttl(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    value = {
        "file_id": 42,
        "name": "test.txt",
    }

    result = redis_client.set(
        "file:42",
        value,
    )

    assert result is True

    mock_redis.set.assert_called_once_with(
        "file:42",
        json.dumps(
            value,
            separators=(",", ":"),
        ),
    )


def test_set_stores_json_with_ttl(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    value = {
        "file_id": 42,
        "name": "test.txt",
    }

    result = redis_client.set(
        "file:42",
        value,
        ttl_seconds=300,
    )

    assert result is True

    mock_redis.set.assert_called_once_with(
        "file:42",
        '{"file_id":42,"name":"test.txt"}',
        ex=300,
    )


def test_set_rejects_zero_ttl(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(ValueError, match="TTL must be greater than zero"):
        redis_client.set(
            "file:42",
            {"name": "test.txt"},
            ttl_seconds=0,
        )

    mock_redis.set.assert_not_called()


def test_set_rejects_negative_ttl(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(ValueError, match="TTL must be greater than zero"):
        redis_client.set(
            "file:42",
            {"name": "test.txt"},
            ttl_seconds=-1,
        )

    mock_redis.set.assert_not_called()


def test_delete_removes_key(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    result = redis_client.delete("file:42")

    assert result == 1

    mock_redis.delete.assert_called_once_with("file:42")


def test_close_calls_close(mock_redis):
    redis_client = RedisClient("redis://localhost:6379/0")

    redis_client.close()

    mock_redis.close.assert_called_once_with()


def test_redis_error_is_propagated_from_ping(mock_redis):
    from redis.exceptions import RedisError

    mock_redis.ping.side_effect = RedisError("Redis unavailable")

    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(RedisError, match="Redis unavailable"):
        redis_client.ping()


def test_redis_error_is_propagated_from_get(mock_redis):
    from redis.exceptions import RedisError

    mock_redis.get.side_effect = RedisError("Redis unavailable")

    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(RedisError, match="Redis unavailable"):
        redis_client.get("file:42")


def test_redis_error_is_propagated_from_set(mock_redis):
    from redis.exceptions import RedisError

    mock_redis.set.side_effect = RedisError("Redis unavailable")

    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(RedisError, match="Redis unavailable"):
        redis_client.set(
            "file:42",
            {"name": "test.txt"},
        )


def test_redis_error_is_propagated_from_delete(mock_redis):
    from redis.exceptions import RedisError

    mock_redis.delete.side_effect = RedisError("Redis unavailable")

    redis_client = RedisClient("redis://localhost:6379/0")

    with pytest.raises(RedisError, match="Redis unavailable"):
        redis_client.delete("file:42")