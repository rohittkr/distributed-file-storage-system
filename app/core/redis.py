from __future__ import annotations

import json
from typing import Any

import redis
from redis import Redis

from app.core.config import settings


class RedisClient:
    """Small application-level abstraction around Redis."""

    def __init__(
        self,
        url: str | None = None,
    ) -> None:
        self._url = url or settings.redis_url
        self._client: Redis[str] = redis.Redis.from_url(
            self._url,
            decode_responses=True,
        )

    def ping(self) -> bool:
        """Return True when Redis is reachable."""
        return bool(self._client.ping())

    def get(self, key: str) -> Any | None:
        """Get a JSON-encoded value from Redis."""
        value = self._client.get(key)
        if value is None:
            return None
        return json.loads(value)

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Store a JSON-serializable value with an optional TTL."""
        serialized_value = json.dumps(
            value,
            separators=(",", ":"),
        )

        if ttl_seconds is None:
            return bool(
                self._client.set(
                    key,
                    serialized_value,
                )
            )

        if ttl_seconds <= 0:
            raise ValueError("TTL must be greater than zero.")

        return bool(
            self._client.set(
                key,
                serialized_value,
                ex=ttl_seconds,
            )
        )

    def set_if_not_exists(
        self,
        key: str,
        value: Any,
        ttl_seconds: int,
    ) -> bool:
        """Set a JSON-encoded value only when the key does not exist."""
        if ttl_seconds <= 0:
            raise ValueError("TTL must be greater than zero.")

        serialized_value = json.dumps(
            value,
            separators=(",", ":"),
        )

        return bool(
            self._client.set(
                key,
                serialized_value,
                ex=ttl_seconds,
                nx=True,
            )
        )

    def release_if_owner(
        self,
        key: str,
        owner_token: str,
    ) -> bool:
        """Delete a lock only when its value matches the owner token."""
        serialized_owner_token = json.dumps(
            owner_token,
            separators=(",", ":"),
        )

        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """

        result = self._client.eval(
            script,
            1,
            key,
            serialized_owner_token,
        )

        return bool(result)

    def delete(self, key: str) -> int:
        """Delete a Redis key and return the number of keys removed."""
        return int(self._client.delete(key))

    def close(self) -> None:
        """Close the Redis connection pool."""
        self._client.close()


redis_client = RedisClient()