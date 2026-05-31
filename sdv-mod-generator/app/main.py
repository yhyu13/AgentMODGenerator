"""FastAPI application entry point."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_dotenv_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(_dotenv_path, override=False)

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
        logger.warning("startup.init_db.skipped", reason=str(exc))

    bot_task: asyncio.Task | None = None
    from app.config import get_config
    if get_config().discord_bot_token:
        from app.discord.bot import start_bot
        bot_task = asyncio.create_task(start_bot())
        logger.info("startup.discord_bot.started")

    yield

    if bot_task:
        from app.discord.bot import get_bot
        bot = get_bot()
        bot_task.cancel()
        if bot:
            try:
                await bot.close()
            except Exception:
                pass
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        logger.info("startup.discord_bot.stopped")

    try:
        from storage.redis import close_client
        await close_client()
    except Exception:
        pass

    try:
        from storage.postgres import close_pool
        await close_pool()
    except Exception:
        pass


app = FastAPI(title="SDV Mod Generator", version="0.1.0", lifespan=lifespan)

app.include_router(api_router)


@app.post("/webhooks/discord")
async def discord_webhook(request: Request) -> dict[str, Any]:
    from app.discord.webhook import handle_interaction
    return await handle_interaction(request)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/v1/mods/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    logger.info(
        "api.generate",
        request_id=request_id,
        user_id=req.user_id,
        prompt=req.prompt,
    )
    try:
        await create_mod_request(
            request_id=request_id,
            user_id=req.user_id,
            prompt=req.prompt,
            phase=req.phase or "p1_shop_channel",
            generators=[],
            hint={},
        )

        result = await run_pipeline(request_id, req.user_id, req.prompt)

        await update_mod_request_status(request_id, result.status)
        await set_pipeline_state(request_id, {
            "status": result.status,
            "errors": result.errors,
            "outputs": {
                name: {
                    "files": out.files,
                    "assets": out.assets,
                    "metadata": out.metadata,
                }
                for name, out in (result.outputs or {}).items()
            },
            "t2_feedback": result.t2_feedback,
            "t2_score": result.t2_score,
            "zip_key": result.zip_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        if result.status == "failed":
            await save_mod_output(
                request_id=request_id,
                zip_key=None,
                zip_url=None,
                files_preview=[],
                t1_errors=result.errors,
                t2_feedback=result.t2_feedback,
                t2_score=result.t2_score,
            )
            return GenerateResponse(request_id=request_id, status="failed")

        files_preview = list(result.outputs.keys())
        await save_mod_output(
            request_id=request_id,
            zip_key=result.zip_key,
            zip_url=None,
            files_preview=files_preview,
            t1_errors=result.errors,
            t2_feedback=result.t2_feedback,
            t2_score=result.t2_score,
        )

        return GenerateResponse(request_id=request_id, status="done")

    except Exception as exc:
        logger.error("api.generate.error", request_id=request_id, error=str(exc))
        await update_mod_request_status(request_id, "failed")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")