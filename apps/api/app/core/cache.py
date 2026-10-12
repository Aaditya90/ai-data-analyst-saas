"""
First real use of Redis in this project (it's been in docker-compose since
Phase 1 but unused until now). EDA results are an ideal caching candidate:
DatasetVersions are immutable once READY (see DatasetVersion's docstring),
so a profile computed for a given version_id is valid forever — there's no
staleness/invalidation problem to solve, only a "don't recompute" one.
"""

import json
from functools import lru_cache
from typing import Any

import redis

from app.core.config import get_settings


@lru_cache
def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def get_json(key: str) -> Any | None:
    try:
        raw = get_redis_client().get(key)
    except redis.RedisError:
        # Cache being unreachable should degrade to "recompute", not 500
        return None
    return json.loads(raw) if raw is not None else None


def set_json(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    try:
        get_redis_client().set(key, json.dumps(value), ex=ttl_seconds)
    except redis.RedisError:
        pass  # caching is an optimization, not a correctness requirement
