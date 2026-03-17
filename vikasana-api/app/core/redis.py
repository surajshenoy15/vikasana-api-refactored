# app/core/redis.py — Redis caching + rate limiting + session validation
import json
import hashlib
from typing import Any, Optional
from functools import wraps

import redis.asyncio as aioredis
from app.core.config import settings


# ── Redis Connection Pool (shared across all instances) ──

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


async def close_redis():
    global _redis_pool
    if _redis_pool:
        await _redis_pool.close()
        _redis_pool = None


# ── Cache Helpers ──

async def cache_get(key: str) -> Optional[Any]:
    r = await get_redis()
    val = await r.get(key)
    if val:
        return json.loads(val)
    return None


async def cache_set(key: str, value: Any, ttl: int = 300):
    r = await get_redis()
    await r.set(key, json.dumps(value, default=str), ex=ttl)


async def cache_delete(key: str):
    r = await get_redis()
    await r.delete(key)


async def cache_delete_pattern(pattern: str):
    r = await get_redis()
    async for key in r.scan_iter(match=pattern):
        await r.delete(key)


# ── Rate Limiting ──

async def rate_limit_check(identifier: str, max_requests: int = 100, window_seconds: int = 60) -> bool:
    """
    Returns True if request is allowed, False if rate-limited.
    Uses sliding window counter with Redis.
    """
    r = await get_redis()
    key = f"ratelimit:{identifier}"
    current = await r.incr(key)
    if current == 1:
        await r.expire(key, window_seconds)
    return current <= max_requests


# ── Token Validation Cache ──

async def cache_token_validation(token_hash: str, user_data: dict, ttl: int = 300):
    """Cache decoded token data to avoid repeated DB lookups."""
    key = f"token:{token_hash}"
    await cache_set(key, user_data, ttl)


async def get_cached_token_validation(token_hash: str) -> Optional[dict]:
    key = f"token:{token_hash}"
    return await cache_get(key)


# ── Cache Decorator for Endpoints ──

def cached_endpoint(prefix: str, ttl: int = 300):
    """
    Decorator for caching endpoint responses.

    Usage:
        @cached_endpoint("dashboard:stats", ttl=60)
        async def get_stats(db: AsyncSession):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key from function args
            key_parts = [prefix]
            for k, v in sorted(kwargs.items()):
                if k != "db":
                    key_parts.append(f"{k}={v}")
            cache_key = ":".join(key_parts)

            # Try cache first
            cached = await cache_get(cache_key)
            if cached is not None:
                return cached

            # Execute and cache
            result = await func(*args, **kwargs)
            await cache_set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator
