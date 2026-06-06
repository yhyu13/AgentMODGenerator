"""FastAPI application entry point."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request

from app.api.routes import router as api_router
from app.api.schemas import GenerateRequest, GenerateResponse
from orchestrator.pipeline import run_pipeline
from storage.queries import (
    create_mod_request,
    update_mod_request_status,
    save_mod_output,
)
from storage.redis import set_pipeline_state

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        from app.discord.bot import get_bot
        bot = get_bot()
        bot_task.cancel()
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

app.include_router(api_router)


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


