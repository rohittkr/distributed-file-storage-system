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

    def delete(self, key: str) -> int:
        """Delete a Redis key and return the number of keys removed."""
        return int(self._client.delete(key))

    def close(self) -> None:
        """Close the Redis connection pool."""
        self._client.close()


redis_client = RedisClient()