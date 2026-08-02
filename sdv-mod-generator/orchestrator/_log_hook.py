"""Pipeline log capture — bridge between orchestrator structlog events and Redis.

Companion to :func:`storage.redis.append_pipeline_log` (v75 write
side) and :func:`GET /v1/mods/{id}/logs` (v75 read side). The
orchestrator emits ~25 structlog events per generation; this
module forwards each to the Redis stream so the endpoint has data
to return.

Two surfaces: ``emit_pipeline_log`` (sync, fire-and-forget) for
sync nodes (``node_route``, ``node_t1_gate``); ``emit_pipeline_log_async``
for async call sites (``run_pipeline`` entry/exit) that want the
log persisted before returning.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog


logger = structlog.get_logger()


def emit_pipeline_log(request_id: str, level: str, event: str, **fields: Any) -> None:
    """Sync emit: structlog fires immediately; Redis append is scheduled on the
    running loop if available. Sync nodes without a loop (e.g. unit tests
    that call ``node_route(state)`` directly) silently skip the Redis side.

    Args:
        request_id: Pipeline request id (Redis list key suffix).
        level: ``info`` / ``warning`` / ``error`` / ``debug`` (lowercase,
            matching structlog's BoundLogger). Anything else falls back
            to ``info`` — a typo must not raise ``AttributeError``.
        event: Dot.case event name (e.g. ``pipeline.routing.done``).
        **fields: Forwarded to structlog AND to the Redis ``extras`` dict.
    """
    log_fn = getattr(logger, level.lower(), None) or logger.info
    log_fn(event, **fields)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _append_async() -> None:
        from storage.redis import append_pipeline_log
        try:
            await append_pipeline_log(
                request_id=request_id,
                level=level.upper(),
                event=event,
                message=str(fields.get("message", "")) if "message" in fields else "",
                extra=fields,
            )
        except Exception as exc:
            # A real Redis outage during a request must look like "logging
            # broken", not "no logs". Previously swallowed with bare
            # ``except: pass`` — a silent black hole for pipeline logs.
            logger.warning(
                "pipeline_log.redis_append_failed",
                request_id=request_id,
                log_event=event,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    try:
        loop.create_task(_append_async())
    except RuntimeError as exc:
        logger.warning(
            "pipeline_log.no_running_loop",
            request_id=request_id,
            log_event=event,
            error=str(exc),
        )


async def emit_pipeline_log_async(
    request_id: str,
    level: str,
    event: str,
    **fields: Any,
) -> None:
    """Async variant — awaits the Redis append before returning. Used from
    ``run_pipeline`` so the start event is in the Redis list before the
    coroutine returns to the caller.
    """
    log_fn = getattr(logger, level.lower(), None) or logger.info
    log_fn(event, **fields)

    from storage.redis import append_pipeline_log
    try:
        await append_pipeline_log(
            request_id=request_id,
            level=level.upper(),
            event=event,
            message=str(fields.get("message", "")) if "message" in fields else "",
            extra=fields,
        )
    except Exception as exc:
        logger.warning(
            "pipeline_log.redis_append_failed",
            request_id=request_id,
            log_event=event,
            error=str(exc),
            error_type=type(exc).__name__,
        )