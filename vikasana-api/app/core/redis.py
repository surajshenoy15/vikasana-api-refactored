# app/core/redis.py — Redis caching + rate limiting + session validation
import json
import logging
from typing import Any, Optional
from functools import wraps

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

from app.core.config import settings

logger = logging.getLogger(__name__)

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
        try:
            await _redis_pool.close()
        except Exception:
            logger.exception("Redis close failed")
        finally:
            _redis_pool = None


def _redis_unavailable(exc: Exception):
    logger.warning("Redis unavailable; continuing without cache/rate-limit. Error: %s", exc)


# ── Cache Helpers ──

async def cache_get(key: str) -> Optional[Any]:
    """
    Fail-open cache read.
    If Redis is unavailable, return None and let API continue normally.
    """
    try:
        r = await get_redis()
        val = await r.get(key)
        if val:
            return json.loads(val)
        return None
    except (RedisError, RedisConnectionError, OSError, TimeoutError) as exc:
        _redis_unavailable(exc)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("Redis cache JSON decode failed for key=%s: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: int = 300):
    """
    Fail-open cache write.
    If Redis is unavailable, silently skip caching.
    """
    try:
        r = await get_redis()
        await r.set(key, json.dumps(value, default=str), ex=ttl)
    except (RedisError, RedisConnectionError, OSError, TimeoutError) as exc:
        _redis_unavailable(exc)


async def cache_delete(key: str):
    try:
        r = await get_redis()
        await r.delete(key)
    except (RedisError, RedisConnectionError, OSError, TimeoutError) as exc:
        _redis_unavailable(exc)


async def cache_delete_pattern(pattern: str):
    try:
        r = await get_redis()
        async for key in r.scan_iter(match=pattern):
            await r.delete(key)
    except (RedisError, RedisConnectionError, OSError, TimeoutError) as exc:
        _redis_unavailable(exc)


# ── Rate Limiting ──

async def rate_limit_check(identifier: str, max_requests: int = 100, window_seconds: int = 60) -> bool:
    """
    Returns True if request is allowed, False if rate-limited.

    Fail-open behavior:
    If Redis is unavailable, allow the request instead of crashing the API.
    For production hard rate limiting, attach a real Redis/ElastiCache endpoint.
    """
    try:
        r = await get_redis()
        key = f"ratelimit:{identifier}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, window_seconds)
        return current <= max_requests
    except (RedisError, RedisConnectionError, OSError, TimeoutError) as exc:
        _redis_unavailable(exc)
        return True


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
            key_parts = [prefix]
            for k, v in sorted(kwargs.items()):
                if k != "db":
                    key_parts.append(f"{k}={v}")
            cache_key = ":".join(key_parts)

            cached = await cache_get(cache_key)
            if cached is not None:
                return cached

            result = await func(*args, **kwargs)
            await cache_set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator
