"""
Rate limiting (tiêu chí 7) — Sliding window 60 giây lưu trong Redis Sorted Set.
State nằm trong Redis nên dùng chung giữa các worker/instance và bền vững sau khi restart.
"""
import hashlib
import logging
import time
import uuid
from collections import defaultdict, deque

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger("app.rate_limiter")

WINDOW_SECONDS = 60

# Atomic sliding-window rate limit trên Redis (ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE)
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry = window
    if #oldest >= 2 then
        retry = math.max(1, math.ceil(tonumber(oldest[2]) + window - now))
    end
    return {0, count, retry}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 5)
return {1, count + 1, 0}
"""

# Fallback cục bộ chỉ dùng khi Redis hoàn toàn không kết nối được
_fallback_windows: dict[str, deque] = defaultdict(deque)


def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


def check_rate_limit(key: str) -> dict:
    """
    Kiểm tra rate limit theo API key (tối đa RATE_LIMIT_PER_MINUTE / 60s).
    Vượt giới hạn → raise HTTPException(429) kèm header Retry-After.
    """
    limit = settings.rate_limit_per_minute
    now = time.time()
    redis_key = f"rate_limit:{_key_hash(key)}"
    client = get_redis()

    if client is not None:
        try:
            member = f"{now}:{uuid.uuid4().hex}"
            allowed, count, retry_after = client.eval(
                _SLIDING_WINDOW_LUA,
                1,
                redis_key,
                str(now),
                str(WINDOW_SECONDS),
                str(limit),
                member,
            )
            if int(allowed) == 0:
                retry_sec = max(1, int(retry_after))
                logger.warning(
                    "Rate limit exceeded",
                    extra={"event": "rate_limit_exceeded", "limit": limit, "retry_after": retry_sec},
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "Rate limit exceeded",
                        "limit": limit,
                        "window_seconds": WINDOW_SECONDS,
                        "retry_after_seconds": retry_sec,
                    },
                    headers={
                        "Retry-After": str(retry_sec),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
            return {
                "limit": limit,
                "remaining": max(0, limit - int(count)),
                "storage": "redis",
            }
        except HTTPException:
            raise
        except redis.RedisError as exc:
            logger.warning(
                "Redis unavailable for rate limiter; falling back to local window",
                extra={"event": "rate_limit_redis_fallback", "error": str(exc)},
            )

    window = _fallback_windows[redis_key]
    cutoff = now - WINDOW_SECONDS
    while window and window[0] <= cutoff:
        window.popleft()

    if len(window) >= limit:
        retry_sec = max(1, int(window[0] + WINDOW_SECONDS - now) + 1)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_sec,
            },
            headers={
                "Retry-After": str(retry_sec),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    window.append(now)
    return {
        "limit": limit,
        "remaining": max(0, limit - len(window)),
        "storage": "memory_fallback",
    }
