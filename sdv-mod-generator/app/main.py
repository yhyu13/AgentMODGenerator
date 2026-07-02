"""FastAPI application entry point."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import Response

from app.api.routes import router as api_router
from app.logging_config import get_logger
from app.metrics import (
    API_REQUEST_DURATION_SECONDS,
    API_REQUESTS_TOTAL,
    render_metrics,
)
from app.middleware import RequestIdMiddleware, SecurityHeadersMiddleware

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import APP_ENV, require_prod_secrets

    if APP_ENV in ("prod", "production"):
        try:
            require_prod_secrets()
        except RuntimeError as exc:
            logger.error("startup.config.prod_secrets.missing", error=str(exc))
            raise

    try:
        from app.config import validate_config

        validate_config()
    except RuntimeError as exc:
        logger.error("startup.config.validation_failed", error=str(exc))
        raise RuntimeError("Configuration validation failed - cannot start") from exc

    try:
        from storage.postgres import init_db
        await init_db()
    except Exception as exc:
        logger.error("startup.init_db.failed", reason=str(exc))
        raise RuntimeError("Database initialization failed - cannot start") from exc

    bot_task: asyncio.Task | None = None
    from app.config import get_config
    if get_config().discord_bot_token:
        from app.discord.bot import start_bot
        bot_task = asyncio.create_task(start_bot())
        # Log if the bot task dies so silent failures are visible
        def _on_bot_done(t: asyncio.Task) -> None:
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                logger.info("startup.discord_bot.cancelled")
                return
            if exc is not None:
                logger.error("startup.discord_bot.crashed", error=str(exc), error_type=type(exc).__name__)
            else:
                logger.info("startup.discord_bot.stopped_cleanly")
        bot_task.add_done_callback(_on_bot_done)
        logger.info("startup.discord_bot.started")

    yield

    if bot_task:
        from app.discord.bot import get_bot, get_notifier
        bot = get_bot()
        notifier = get_notifier()
        bot_task.cancel()
        if notifier:
            await notifier.stop()
        if bot:
            try:
                await bot.close()
            except Exception as exc:
                logger.warning("startup.discord_bot.close.failed", error=str(exc))
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        logger.info("startup.discord_bot.stopped")

    try:
        from storage.redis import close_client
        await close_client()
    except Exception as exc:
        logger.warning("startup.redis.close.failed", error=str(exc))

    try:
        from storage.postgres import close_pool
        await close_pool()
    except Exception as exc:
        logger.warning("startup.postgres.close.failed", error=str(exc))


app = FastAPI(title="SDV Mod Generator", version="0.1.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.include_router(api_router)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Count every request and record its latency under the route template.

    We label by `request.scope["route"].path` when available, falling back
    to `request.url.path` for unmatched paths. Cardinality stays bounded:
    per-request-id or per-query-string labels are deliberately not used.
    """
    started = time.perf_counter()
    response: Response = await call_next(request)
    duration = time.perf_counter() - started

    route = request.scope.get("route")
    path_label = getattr(route, "path", request.url.path)
    method = request.method
    status = str(response.status_code)

    API_REQUESTS_TOTAL.labels(method=method, path=path_label, status=status).inc()
    API_REQUEST_DURATION_SECONDS.labels(method=method, path=path_label).observe(duration)
    return response


@app.post("/webhooks/discord")
async def discord_webhook(request: Request) -> dict[str, Any]:
    from app.discord.webhook import handle_interaction
    return await handle_interaction(request)


@app.get("/health")
def health() -> dict[str, Any]:
    from app.discord.bot import is_bot_ready
    return {
        "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
        "discord_bot_ready": is_bot_ready(),
    }


@app.get("/health/deep")
async def health_deep() -> Response:
    """Deep readiness — pings DB, Redis, S3, and the Discord gateway.

    Returns 200 with body `{"status":"ok",...}` when all probes pass.
    Returns 503 with body `{"status":"degraded","checks":[...]}` otherwise.
    """
    from app.health import deep_health

    status_code, body = await deep_health()
    import json

    return Response(
        content=json.dumps(body, default=str),
        status_code=status_code,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus scrape endpoint. Content-type is the standard text format."""
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
