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
    cfg = get_config()
    # v110 — soft warning when ``APP_ENV=prod`` but the Discord bot
    # isn't configured. The bot is optional in dev (e.g. local
    # restart-loop testing of the API alone) but a production deploy
    # without Discord configured almost certainly means an operator
    # missed setting ``DISCORD_BOT_TOKEN`` in their secrets mount —
    # so we log a WARNING (not raise) to flag the misconfiguration
    # without breaking the deploy path. Uses the new
    # ``cfg.discord_bot_configured`` bool (v110) instead of
    # ``bool(cfg.discord_bot_token)`` so the intent is explicit and
    # the strip() semantics are centralized on the config layer.
    if not cfg.discord_bot_configured and APP_ENV in ("prod", "production"):
        logger.warning(
            "startup.discord_bot.unconfigured_in_prod",
            app_env=APP_ENV,
            hint="Set DISCORD_BOT_TOKEN to enable the Discord gateway",
        )
    # v112 — soft warning when ``APP_ENV=prod`` but the Discord
    # application ID isn't configured. Mirrors the v110
    # ``discord_bot_configured`` block above: ``require_prod_secrets``
    # already enforces ``DISCORD_APP_ID`` is non-empty in prod (it
    # lives in ``_REQUIRED_PROD_SECRETS``), so this block is the
    # observability complement — it logs a WARNING at startup so the
    # misconfiguration is visible in the deploy log even before the
    # strict prod-secrets check runs (e.g. in ``APP_ENV=staging`` or
    # any future env that opts in to the warning without the strict
    # gate). Uses the v111 ``cfg.discord_app_id_valid`` bool (which
    # strips the value before the truthiness check, defending against
    # the ``bool("  ") is True`` Python 3.11+ truthiness trap).
    if not cfg.discord_app_id_valid and APP_ENV in ("prod", "production"):
        logger.warning(
            "startup.discord_app_id.unconfigured_in_prod",
            app_env=APP_ENV,
            hint="Set DISCORD_APP_ID to identify the Discord application",
        )
    # v112 — soft warning when ``APP_ENV=prod`` but the API key
    # isn't configured. Mirrors the v110 ``discord_bot_configured``
    # block: ``require_prod_secrets`` already enforces ``API_KEY``
    # is non-empty in prod (it lives in ``_REQUIRED_PROD_SECRETS``),
    # so this block is the observability complement — it logs a
    # WARNING at startup so the misconfiguration is visible in the
    # deploy log. Without ``API_KEY`` every authenticated endpoint
    # returns 503 via ``verify_api_key`` (``app/api/routes.py:99-105``
    # checks ``if not cfg.api_key: return False``), so the runtime
    # path catches the misconfiguration too — but a startup WARNING
    # surfaces it earlier and without a client request needing to
    # hit the API first. Uses the v111 ``cfg.api_key_configured``
    # bool (strips before truthiness, same Python 3.11+ guard as
    # ``discord_app_id_valid``).
    if not cfg.api_key_configured and APP_ENV in ("prod", "production"):
        logger.warning(
            "startup.api_key.unconfigured_in_prod",
            app_env=APP_ENV,
            hint="Set API_KEY to enable authenticated endpoints",
        )
    # v113 — soft warning when ``APP_ENV=prod`` but the API owner
    # user ID isn't configured. Mirrors the v110
    # ``discord_bot_configured`` and v112 ``api_key_configured``
    # blocks above: ``require_prod_secrets`` already enforces
    # ``API_OWNER_USER_ID`` is non-empty in prod (it lives in
    # ``_REQUIRED_PROD_SECRETS``), so this block is the
    # observability complement — it logs a WARNING at startup so
    # the misconfiguration is visible in the deploy log even before
    # the strict prod-secrets check runs (e.g. in ``APP_ENV=staging``
    # or any future env that opts in to the warning without the
    # strict gate). The owner ID gates the per-user authorization
    # on ``GET /v1/users/{id}/history`` — when unset, the owner
    # gate is disabled and the endpoint returns the path's
    # ``user_id``'s history directly (dev-friendly default); when
    # set, the endpoint returns 403 on a mismatched ``user_id``.
    # Uses the v113 ``cfg.api_owner_configured`` bool (strips
    # before truthiness, same Python 3.11+ guard as the v111
    # ``discord_app_id_valid`` and ``api_key_configured`` bools).
    # Closes the v110/v111/v112 bool-wrapper rollout on every
    # operator-facing string secret in ``_REQUIRED_PROD_SECRETS``
    # (database / redis / s3 / discord_bot / discord_app_id /
    # api_key / api_owner_user_id).
    if not cfg.api_owner_configured and APP_ENV in ("prod", "production"):
        logger.warning(
            "startup.api_owner.unconfigured_in_prod",
            app_env=APP_ENV,
            hint=(
                "Set API_OWNER_USER_ID to enable per-user authorization "
                "on /v1/users/{id}/history"
            ),
        )
    if cfg.discord_bot_token:
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
