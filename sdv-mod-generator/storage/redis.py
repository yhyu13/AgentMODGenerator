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
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = redis.from_url(_REDIS_URL, decode_responses=True)
                logger.info("storage.redis.connected", url=_REDIS_URL)
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
    logger.info("storage.redis.get_pipeline_state", request_id=request_id, hit=True)
    return json.loads(data)


async def close_client() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
        logger.info("storage.redis.closed")
