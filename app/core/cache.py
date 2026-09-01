from typing import Any

from app.core.redis import redis_client


FILE_CACHE_TTL_SECONDS = 300


def file_cache_key(file_id: int, owner_id: int) -> str:
    return f"file:{owner_id}:{file_id}"


def get_cached_file(
    file_id: int,
    owner_id: int,
) -> Any | None:
    return redis_client.get(
        file_cache_key(file_id, owner_id)
    )


def cache_file(
    file_id: int,
    owner_id: int,
    value: Any,
) -> bool:
    return redis_client.set(
        file_cache_key(file_id, owner_id),
        value,
        ttl_seconds=FILE_CACHE_TTL_SECONDS,
    )


def invalidate_file_cache(
    file_id: int,
    owner_id: int,
) -> int:
    return redis_client.delete(
        file_cache_key(file_id, owner_id)
    )