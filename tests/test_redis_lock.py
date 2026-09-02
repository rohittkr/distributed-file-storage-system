from unittest.mock import MagicMock, patch

import pytest

from app.core.redis import RedisClient
from concurrent.futures import ThreadPoolExecutor


def test_set_if_not_exists_acquires_lock_when_key_is_missing():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.set.return_value = True
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.set_if_not_exists(
            "lock:test",
            "owner-token",
            ttl_seconds=30,
        )

        assert result is True
        client.set.assert_called_once_with(
            "lock:test",
            '"owner-token"',
            ex=30,
            nx=True,
        )


def test_set_if_not_exists_returns_false_when_key_already_exists():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.set.return_value = False
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.set_if_not_exists(
            "lock:test",
            "owner-token",
            ttl_seconds=30,
        )

        assert result is False
        client.set.assert_called_once_with(
            "lock:test",
            '"owner-token"',
            ex=30,
            nx=True,
        )


def test_set_if_not_exists_serializes_dictionary():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.set.return_value = True
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.set_if_not_exists(
            "lock:test",
            {"request_id": 42},
            ttl_seconds=60,
        )

        assert result is True
        client.set.assert_called_once_with(
            "lock:test",
            '{"request_id":42}',
            ex=60,
            nx=True,
        )


def test_set_if_not_exists_rejects_zero_ttl():
    with patch("app.core.redis.redis.Redis.from_url"):
        redis_client = RedisClient("redis://localhost:6379/0")

        with pytest.raises(ValueError, match="TTL must be greater than zero"):
            redis_client.set_if_not_exists(
                "lock:test",
                "owner-token",
                ttl_seconds=0,
            )


def test_set_if_not_exists_rejects_negative_ttl():
    with patch("app.core.redis.redis.Redis.from_url"):
        redis_client = RedisClient("redis://localhost:6379/0")

        with pytest.raises(ValueError, match="TTL must be greater than zero"):
            redis_client.set_if_not_exists(
                "lock:test",
                "owner-token",
                ttl_seconds=-1,
            )

def test_release_if_owner_deletes_lock_when_token_matches():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.eval.return_value = 1
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.release_if_owner(
            "lock:test",
            "owner-token",
        )

        assert result is True
        client.eval.assert_called_once()
        call_args = client.eval.call_args.args

        assert call_args[1] == 1
        assert call_args[2] == "lock:test"
        assert call_args[3] == '"owner-token"'


def test_release_if_owner_returns_false_when_token_does_not_match():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.eval.return_value = 0
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.release_if_owner(
            "lock:test",
            "different-owner",
        )

        assert result is False
        client.eval.assert_called_once()


def test_release_if_owner_returns_false_when_lock_is_missing():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.eval.return_value = 0
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        result = redis_client.release_if_owner(
            "lock:missing",
            "owner-token",
        )

        assert result is False

def test_set_if_not_exists_allows_only_one_owner():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        client.set.side_effect = [True, False]
        mock_from_url.return_value = client

        redis_client = RedisClient("redis://localhost:6379/0")

        first_result = redis_client.set_if_not_exists(
            "lock:test",
            "owner-a",
            ttl_seconds=30,
        )

        second_result = redis_client.set_if_not_exists(
            "lock:test",
            "owner-b",
            ttl_seconds=30,
        )

        assert first_result is True
        assert second_result is False

        assert client.set.call_count == 2

        client.set.assert_any_call(
            "lock:test",
            '"owner-a"',
            ex=30,
            nx=True,
        )

        client.set.assert_any_call(
            "lock:test",
            '"owner-b"',
            ex=30,
            nx=True,
        )



def test_set_if_not_exists_allows_only_one_concurrent_acquisition():
    with patch("app.core.redis.redis.Redis.from_url") as mock_from_url:
        client = MagicMock()
        mock_from_url.return_value = client

        results = [True, False]

        def set_side_effect(*args, **kwargs):
            return results.pop(0)

        client.set.side_effect = set_side_effect

        redis_client = RedisClient(
            "redis://localhost:6379/0"
        )

        def acquire_lock():
            return redis_client.set_if_not_exists(
                "lock:concurrent-test",
                "owner-token",
                ttl_seconds=30,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(acquire_lock)
                for _ in range(2)
            ]

            results_from_threads = [
                future.result()
                for future in futures
            ]

        assert sorted(results_from_threads) == [False, True]