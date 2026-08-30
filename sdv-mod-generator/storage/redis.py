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


async def set_cancellation_reason(request_id: str, reason: str, ttl: int = 3600) -> None:
    """Write the cancellation reason for a cancelled mod request.

    Mirrors the pattern of :func:`set_status`: a short-TTL key
    (``mod:cancel_reason:{request_id}``) holding the reason string.
    The default 1 hour TTL is consistent with the status field so the
    reason and status expire together — callers should not assume the
    reason outlives the status.

    Pairs with :func:`get_cancellation_reason`. The key namespace
    (``mod:cancel_reason:``) is intentionally distinct from the status
    key (``mod:status:``) so a future move to a longer TTL or a
    separate cleanup policy can be applied to one without the other.
    """
    client = await get_client()
    key = f"mod:cancel_reason:{request_id}"
    await client.set(key, reason, ex=ttl)
    logger.info(
        "storage.redis.set_cancellation_reason",
        request_id=request_id,
        reason=reason,
        ttl=ttl,
    )


async def get_cancellation_reason(request_id: str) -> str | None:
    """Read the cancellation reason for a mod request, or ``None``.

    Returns the stored reason string for a previously-cancelled
    request, or ``None`` if no reason was recorded (either because the
    request is not cancelled, or because it was cancelled before this
    field existed — pre-feature legacy cancellations will return
    ``None``). Callers should treat ``None`` as "unknown / not
    applicable" rather than as an error.
    """
    client = await get_client()
    key = f"mod:cancel_reason:{request_id}"
    data = await client.get(key)
    if data is None:
        logger.info(
            "storage.redis.get_cancellation_reason",
            request_id=request_id,
            hit=False,
        )
        return None
    logger.info(
        "storage.redis.get_cancellation_reason",
        request_id=request_id,
        hit=True,
    )
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


# Default cap on retained log entries per request. Picked so the
# Redis value stays well under 1 MB even for verbose prompts
# (a LogEntry is ~250-400 bytes when serialized; 500 entries is
# ~150 KB worst-case, comfortably under the 512 MB string cap).
_PIPELINE_LOG_MAX_ENTRIES = 500


def _pipeline_log_key(request_id: str) -> str:
    """Return the Redis list key holding this request's log stream."""
    return f"pipeline:logs:{request_id}"


async def append_pipeline_log(
    request_id: str,
    level: str,
    event: str,
    message: str,
    extra: dict | None = None,
    ttl: int = 86400,
    max_entries: int = _PIPELINE_LOG_MAX_ENTRIES,
) -> None:
    """Append a single log entry to the request's log stream.

    Uses ``LPUSH`` so the most recent entry is at index 0, and
    ``LTRIM`` to cap the list at ``max_entries`` (oldest entries
    fall off the end). The list expires after ``ttl`` seconds
    (default 24 h, matching :func:`set_pipeline_state`).

    The entry shape mirrors a structlog event dict: ``timestamp``
    (ISO-8601 UTC), ``level`` (INFO|WARNING|ERROR|DEBUG), ``event``
    (dot.case.name), ``message`` (human-readable string), and an
    optional ``extra`` mapping for arbitrary context fields. We
    store this as a JSON string per entry so the list can be read
    back with a single ``LRANGE`` and parsed per-element (cheap).

    ``extra`` keys shadowing the reserved keys (``timestamp``,
    ``level``, ``event``, ``message``) are silently dropped to
    keep the on-the-wire shape stable — the endpoint already
    defensively skips entries that fail to parse, but the
    shadow-drop keeps the read path fast on a well-formed input.
    """
    from datetime import datetime, timezone

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        "message": message,
    }
    if extra:
        for k, v in extra.items():
            if k in payload:
                continue
            payload[k] = v

    client = await get_client()
    key = _pipeline_log_key(request_id)
    pipe = client.pipeline()
    pipe.lpush(key, json.dumps(payload, default=str))
    pipe.ltrim(key, 0, max_entries - 1)
    pipe.expire(key, ttl)
    await pipe.execute()
    logger.info(
        "storage.redis.append_pipeline_log",
        request_id=request_id,
        log_event=event,
        level=level,
    )


async def get_pipeline_logs(
    request_id: str,
    limit: int = 100,
) -> list[dict]:
    """Read log entries for a request, newest-first.

    Returns an empty list when the key does not exist OR when
    ``limit <= 0``. ``limit`` is clamped to ``[1, _PIPELINE_LOG_MAX_ENTRIES]``
    so a caller can't request the full 500-entry buffer and
    silently truncate on the wire — the worst case is bounded
    by the writer's cap.

    Malformed JSON entries are skipped with a WARNING log rather
    than raised. A list that contains only bad entries therefore
    returns ``[]`` instead of bubbling an exception up to the
    route handler.
    """
    if limit <= 0:
        return []
    clamped = max(1, min(limit, _PIPELINE_LOG_MAX_ENTRIES))

    client = await get_client()
    key = _pipeline_log_key(request_id)
    raw_entries = await client.lrange(key, 0, clamped - 1)
    if not raw_entries:
        logger.info(
            "storage.redis.get_pipeline_logs",
            request_id=request_id,
            hit=False,
            count=0,
        )
        return []

    parsed: list[dict] = []
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "storage.redis.get_pipeline_logs.bad_entry",
                request_id=request_id,
                error=str(exc),
            )
            continue
        if not isinstance(entry, dict):
            logger.warning(
                "storage.redis.get_pipeline_logs.bad_entry_type",
                request_id=request_id,
                entry_type=type(entry).__name__,
            )
            continue
        parsed.append(entry)

    logger.info(
        "storage.redis.get_pipeline_logs",
        request_id=request_id,
        hit=True,
        requested=limit,
        returned=len(parsed),
    )
    return parsed
