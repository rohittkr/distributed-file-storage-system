from typing import Any

from redis.exceptions import RedisError

from app.core.redis import redis_client


FILE_CACHE_TTL_SECONDS = 300


def file_cache_key(file_id: int, owner_id: int) -> str:
    return f"file:{owner_id}:{file_id}"


def get_cached_file(
    file_id: int,
    owner_id: int,
) -> Any | None:
    try:
        return redis_client.get(
            file_cache_key(file_id, owner_id)
        )
    except RedisError:
        return None


def cache_file(
    file_id: int,
    owner_id: int,
    value: Any,
) -> bool:
    try:
        return redis_client.set(
            file_cache_key(file_id, owner_id),
            value,
            ttl_seconds=FILE_CACHE_TTL_SECONDS,
        )
    except RedisError:
        return False


def invalidate_file_cache(
    file_id: int,
    owner_id: int,
) -> int:
    try:
        return redis_client.delete(
            file_cache_key(file_id, owner_id)
        )
    except RedisError:
        return 0