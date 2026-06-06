"""Health probes — /health (liveness) and /health/deep (readiness).

/health:        cheap; the process is up and the lifespan is alive.
/health/deep:   pings every dependency (DB, Redis, S3, Discord gateway).
                Returns 200 if all are reachable, 503 with per-dependency
                status otherwise. Suitable for k8s readinessProbe and
                docker HEALTHCHECK.

The probe itself never raises — failures are recorded as a JSON body and
a 503 status so the orchestrator can act on the response.
"""
import asyncio
import time
from typing import Any

import structlog

logger = structlog.get_logger()


async def _probe(name: str, coro_factory, timeout: float = 2.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(coro_factory(), timeout=timeout)
        return {"name": name, "ok": True, "latency_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        logger.warning("health.probe.failed", dependency=name, error=str(exc))
        return {
            "name": name,
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _ping_db() -> None:
    from sqlalchemy import text

    from storage.postgres import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("SELECT 1"))
        result.scalar_one()


async def _ping_redis() -> None:
    from storage.redis import get_client

    client = await get_client()
    await client.ping()


async def _ping_s3() -> None:
    from storage.s3 import get_client

    # If we're in local mode, the S3 client is None — the local filesystem
    # IS the storage layer, so trivially "up".
    client = get_client()
    if client is None:
        return
    import os

    bucket = os.getenv("S3_BUCKET", "sdv-mod-generator")
    client.head_bucket(Bucket=bucket)


async def _ping_bot() -> dict[str, Any]:
    from app.discord.bot import get_bot, is_bot_ready

    bot = get_bot()
    if bot is None:
        return {"name": "discord_bot", "ok": False, "error": "bot_not_started"}
    if not is_bot_ready():
        return {"name": "discord_bot", "ok": False, "error": "gateway_not_ready"}
    latency_ms = None
    if bot.latency is not None:
        latency_ms = int(bot.latency * 1000)
    return {
        "name": "discord_bot",
        "ok": True,
        "latency_ms": latency_ms,
    }


async def deep_health() -> tuple[int, dict[str, Any]]:
    """Probe every dependency. Returns (http_status, body)."""
    from app.metrics import DEPENDENCY_UP

    db_redis_s3 = await asyncio.gather(
        _probe("postgres", _ping_db),
        _probe("redis", _ping_redis),
        _probe("s3", _ping_s3),
    )
    bot_probe = await _ping_bot()
    probes: list[dict[str, Any]] = [*db_redis_s3, bot_probe]

    for p in probes:
        DEPENDENCY_UP.labels(dependency=p["name"]).set(1 if p["ok"] else 0)

    all_ok = all(p["ok"] for p in probes)
    body: dict[str, Any] = {
        "status": "ok" if all_ok else "degraded",
        "checks": probes,
    }
    return (200 if all_ok else 503), body
