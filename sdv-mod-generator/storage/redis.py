"""Redis storage — async aioredis."""
import asyncio
import json
import os

import structlog
import redis.asyncio as redis

logger = structlog.get_logger()

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_client: redis.Redis | None = None
_client_lock = asyncio.Lock()


async def get_client() -> redis.Redis:
    """Return the singleton async Redis client, creating it if needed.

    Raises ConnectionError if the Redis URL is malformed or unreachable.
    """
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                try:
                    new_client = redis.from_url(_REDIS_URL, decode_responses=True)
                    await new_client.ping()
                    _client = new_client
                    logger.info("storage.redis.connected", url=_REDIS_URL)
                except Exception as exc:
                    logger.error("storage.redis.connection_failed", url=_REDIS_URL, error=str(exc))
                    raise ConnectionError(f"Failed to connect to Redis at {_REDIS_URL}: {exc}") from exc
    return _client


async def set_pipeline_state(request_id: str, state: dict, ttl: int = 86400) -> None:
    """Write pipeline state to Redis."""
    client = await get_client()
    key = f"pipeline:{request_id}"
    await client.set(key, json.dumps(state, default=str), ex=ttl)
    logger.info("storage.redis.set_pipeline_state", request_id=request_id, ttl=ttl)


async def get_pipeline_state(request_id: str) -> dict | None:
    """Read pipeline state from Redis."""
    client = await get_client()
    key = f"pipeline:{request_id}"
    data = await client.get(key)
    if data is None:
        logger.info("storage.redis.get_pipeline_state", request_id=request_id, hit=False)
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        logger.warning(
            "storage.redis.get_pipeline_state.json_error",
            request_id=request_id,
            error=str(exc),
        )
        return None
    logger.info("storage.redis.get_pipeline_state", request_id=request_id, hit=True)
    return parsed


async def set_status(request_id: str, status: str, ttl: int = 3600) -> None:
    """Write mod status to Redis with 1 hour TTL."""
    client = await get_client()
    key = f"mod:status:{request_id}"
    await client.set(key, status, ex=ttl)
    logger.info("storage.redis.set_status", request_id=request_id, status=status, ttl=ttl)


async def get_status(request_id: str) -> str | None:
    """Read mod status from Redis."""
    client = await get_client()
    key = f"mod:status:{request_id}"
    data = await client.get(key)
    if data is None:
        logger.info("storage.redis.get_status", request_id=request_id, hit=False)
        return None
    logger.info("storage.redis.get_status", request_id=request_id, hit=True)
    return data


async def close_client() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
        logger.info("storage.redis.closed")


async def set_notification_target(
    request_id: str,
    user_id: str,
    channel_id: int,
    ttl: int = 3600,
) -> None:
    """Register a Discord user/channel to notify when a request completes.

    The completion notifier watcher reads these keys and DMs the zip on done/failed.
    """
    client = await get_client()
    key = f"discord:notify:{request_id}"
    payload = json.dumps({"user_id": str(user_id), "channel_id": str(channel_id)})
    await client.set(key, payload, ex=ttl)
    logger.info(
        "storage.redis.set_notification_target",
        request_id=request_id, user_id=user_id, channel_id=channel_id,
    )


async def list_pending_notifications() -> list[tuple[str, dict]]:
    """Yield (request_id, target) for every registered notification.

    Uses SCAN to avoid blocking Redis (KEYS is O(N) and forbidden in prod).
    """
    client = await get_client()
    out: list[tuple[str, dict]] = []
    async for key in client.scan_iter(match="discord:notify:*", count=100):
        data = await client.get(key)
        if not data:
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        request_id = key.split(":", 2)[2]  # discord:notify:<id>
        out.append((request_id, payload))
    return out


async def delete_notification_target(request_id: str) -> None:
    client = await get_client()
    await client.delete(f"discord:notify:{request_id}")
    logger.info("storage.redis.delete_notification_target", request_id=request_id)
