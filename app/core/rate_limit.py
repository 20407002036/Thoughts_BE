"""Per-user HTTP rate limiting backed by Redis.

The limiter uses a fixed-window counter (Redis INCR + EXPIRE in a single
pipeline). Fixed windows are simple, cheap, and atomic; the trade-off is a
small boundary effect where a user can do `limit` calls at second 59 of
window N and `limit` more at second 0 of window N+1. For the cost-protection
use case here that is acceptable.

Failure mode: if Redis is unreachable the limiter **fails open** — it logs a
warning and allows the request. The alternative (fail closed) would turn a
broker outage into a full API outage, which is worse than a temporary loss
of cost control. The `RATE_LIMIT_ENABLED` env var is the kill switch for
cases where the operator needs to disable the limiter without touching code.
"""
from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import Callable

import redis.asyncio as redis_async
from fastapi import Depends, HTTPException, Request, status

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _get_redis_client(redis_url: str, loop_id: int) -> redis_async.Redis:
    """Build an async Redis client cached per event loop.

    FastAPI app traffic usually runs on one loop per process, while test
    clients may create and tear down loops between cases. Caching per loop
    avoids reusing a client tied to a closed loop in tests.
    """
    _ = loop_id
    return redis_async.from_url(redis_url, encoding="utf-8", decode_responses=True)


def _bucket_key(key_prefix: str, user_id: str, window_seconds: int) -> str:
    """Compose the Redis key for a user/window bucket.

    The window floor (now // window_seconds) is part of the key, so each
    window gets its own key. Old keys auto-expire via the EXPIRE call below.
    """
    window_floor = int(time.time()) // window_seconds
    return f"rl:{key_prefix}:{user_id}:{window_floor}"


async def check_rate_limit(
    redis_client: redis_async.Redis,
    *,
    key_prefix: str,
    user_id: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Increment the user's bucket and report whether the limit is exceeded.

    Returns (allowed, retry_after_seconds). retry_after is meaningful only
    when allowed is False; it tells the client how long until the bucket
    resets so they can back off.

    On any RedisError or connection failure the function returns (True, 0)
    after logging a warning. The request is allowed through.
    """
    key = _bucket_key(key_prefix, user_id, window_seconds)
    try:
        pipe = redis_client.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        count = int(results[0])
    except (redis_async.RedisError, ConnectionError, OSError, RuntimeError) as exc:
        logger.warning(
            "rate_limit_redis_unavailable",
            extra={"key_prefix": key_prefix, "user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        return True, 0

    if count > limit:
        retry_after = window_seconds - (int(time.time()) % window_seconds)
        return False, max(retry_after, 1)
    return True, 0


def rate_limit(
    *,
    key_prefix: str,
    requests: int,
    window_seconds: int,
) -> Callable:
    """Build a FastAPI dependency that enforces a per-user rate limit.

    Usage in a route:

        @router.post("/ingest")
        async def ingest(
            audio: UploadFile = File(...),
            current_user: AuthenticatedUser = Depends(
                rate_limit(
                    key_prefix="ingest_min",
                    requests=settings.rate_limit_ingest_per_minute,
                    window_seconds=60,
                )
            ),
            ...
        ): ...

    The dependency returns the same AuthenticatedUser that get_current_user
    would, so handlers can stack multiple rate_limit() calls (e.g. per-minute
    and per-hour) by chaining them as separate Depends() entries.
    """
    async def _dep(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> AuthenticatedUser:
        if not settings.rate_limit_enabled:
            return current_user

        redis_client = _get_redis_client(settings.redis_url, id(asyncio.get_running_loop()))
        allowed, retry_after = await check_rate_limit(
            redis_client,
            key_prefix=key_prefix,
            user_id=current_user.user_id,
            limit=requests,
            window_seconds=window_seconds,
        )
        if not allowed:
            logger.info(
                "rate_limit_exceeded",
                extra={
                    "key_prefix": key_prefix,
                    "user_id": current_user.user_id,
                    "limit": requests,
                    "window_seconds": window_seconds,
                    "retry_after": retry_after,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
        return current_user

    return _dep


def ingest_rate_limit() -> Callable:
    """Per-user rate limiter for the audio ingest endpoints.

    Stacks two limits: per-minute and per-hour, both keyed on the same
    ingest_ prefix so they share the user-bucket namespace with the
    settings-driven values. Both limits must pass for the request to
    proceed; the per-minute check runs first so a brief burst gets the
    429 with a short Retry-After.

    The settings values are read inside the dependency via
    `Depends(get_settings)`, so the values are evaluated per-request and
    respond to env-var changes after a process restart (or settings
    cache-clear, as used in tests).
    """
    async def _stack(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> AuthenticatedUser:
        if not settings.rate_limit_enabled:
            return current_user

        redis_client = _get_redis_client(settings.redis_url, id(asyncio.get_running_loop()))

        # Per-minute check
        allowed, retry_after = await check_rate_limit(
            redis_client,
            key_prefix="ingest_min",
            user_id=current_user.user_id,
            limit=settings.rate_limit_ingest_per_minute,
            window_seconds=60,
        )
        if not allowed:
            logger.info(
                "rate_limit_exceeded",
                extra={
                    "key_prefix": "ingest_min",
                    "user_id": current_user.user_id,
                    "limit": settings.rate_limit_ingest_per_minute,
                    "window_seconds": 60,
                    "retry_after": retry_after,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

        # Per-hour check
        allowed, retry_after = await check_rate_limit(
            redis_client,
            key_prefix="ingest_hour",
            user_id=current_user.user_id,
            limit=settings.rate_limit_ingest_per_hour,
            window_seconds=3600,
        )
        if not allowed:
            logger.info(
                "rate_limit_exceeded",
                extra={
                    "key_prefix": "ingest_hour",
                    "user_id": current_user.user_id,
                    "limit": settings.rate_limit_ingest_per_hour,
                    "window_seconds": 3600,
                    "retry_after": retry_after,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

        return current_user

    return _stack
