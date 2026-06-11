"""API routes — P1-impl with real PostgreSQL + Redis."""
import asyncio
import uuid
import secrets
import structlog
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    ModStatusResponse,
    FilePreviewResponse,
    HistoryResponse,
    HistoryEntry,
)
from storage.queries import (
    create_mod_request,
    get_mod_output,
    get_user_history,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/v1", tags=["v1"])


async def verify_api_key(x_api_key: Annotated[str | None, Header()] = None) -> bool:
    from app.config import get_config
    cfg = get_config()
    if not cfg.api_key:
        return True
    if not x_api_key or not secrets.compare_digest(x_api_key, cfg.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return True


@router.post("/mods/generate", response_model=GenerateResponse)
async def generate_mod(req: GenerateRequest) -> GenerateResponse:
    """Start mod generation pipeline (non-blocking). Use /status/{id} to poll."""
    from orchestrator.pipeline import run_pipeline_background
    from storage.redis import set_status as redis_set_status
    from storage.queries import create_mod_request

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    logger.info(
        "api.generate.start",
        request_id=request_id,
        user_id=req.user_id,
        prompt=req.prompt,
    )

    await create_mod_request(request_id, req.user_id, req.prompt, "p1_shop_channel", [], {})
    await redis_set_status(request_id, "running")
    run_pipeline_background(request_id, req.user_id, req.prompt)

    # Rough estimate: shop_channel ~90s, texture ~30s, npc_schedule ~60s
    estimated = 90
    prompt_lower = req.prompt.lower()
    if any(k in prompt_lower for k in ("texture", "sprite", "image")):
        estimated = 30
    elif any(k in prompt_lower for k in ("npc", "schedule", "dialogue")):
        estimated = 60

    return GenerateResponse(request_id=request_id, status="running", estimated_seconds=estimated)


@router.get("/mods/status/{request_id}")
async def get_mod_status_check(request_id: str) -> dict:
    """Get current status from Redis cache."""
    from storage.redis import get_status as redis_get_status

    current_status = await redis_get_status(request_id)
    if current_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Status not found for {request_id}",
        )
    return {"request_id": request_id, "status": current_status}


@router.get("/mods/download/{request_id}")
async def get_mod_download(request_id: str) -> dict:
    """Get presigned S3 download URL for completed mod."""
    from storage.s3 import get_presigned_url

    output = await get_mod_output(request_id)
    if not output:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    if output["status"] != "done":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mod not ready. Current status: {output['status']}",
        )

    zip_key = output.get("zip_key")
    if not zip_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zip file not found",
        )

    download_url = get_presigned_url(zip_key)
    return {"request_id": request_id, "download_url": download_url}


@router.get("/mods/{request_id}", response_model=ModStatusResponse)
async def get_mod_status(request_id: str) -> ModStatusResponse:
    """Get generation status and result for a request.
    
    Cache-first: check Redis first, then PostgreSQL.
    """
    from storage.redis import get_pipeline_state

    redis_state = await get_pipeline_state(request_id)
    if redis_state:
        logger.info("api.status.cache_hit", request_id=request_id)
        # Compute progress from pipeline state
        progress = _compute_progress(redis_state)
        return ModStatusResponse(
            request_id=request_id,
            status=redis_state.get("status", "pending"),
            zip_url=redis_state.get("zip_key"),
            files_preview=[f for out in redis_state.get("outputs", {}).values() for f in out.get("files", {}).keys()],
            t1_errors=redis_state.get("errors", []),
            generators_failed=redis_state.get("generators_failed", []),
            generators_succeeded=redis_state.get("generators_succeeded", []),
            t2_feedback=redis_state.get("t2_feedback"),
            t2_score=redis_state.get("t2_score"),
            t2_max_score=redis_state.get("t2_max_score"),
            t2_pass_threshold=redis_state.get("t2_pass_threshold"),
            t2_passed=redis_state.get("t2_passed"),
            t2_available=redis_state.get("t2_available"),
            t2_panel_passed_count=redis_state.get("t2_panel_passed_count"),
            progress_percent=progress["percent"],
            current_stage=progress["stage"],
            created_at=redis_state.get("created_at", datetime.now(timezone.utc).isoformat()),
        )

    output = await get_mod_output(request_id)
    if not output:
        logger.warning("api.status.not_found", request_id=request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    logger.info("api.status.db_hit", request_id=request_id)
    return ModStatusResponse(
        request_id=request_id,
        status=output["status"],
        zip_url=output.get("zip_url"),
        files_preview=output.get("files_preview", []),
        t1_errors=output.get("t1_errors", []),
        t2_feedback=output.get("t2_feedback"),
        t2_score=output.get("t2_score"),
        progress_percent=None,
        current_stage=None,
        created_at=(
            output["created_at"].isoformat()
            if isinstance(output["created_at"], datetime)
            else str(output["created_at"])
        ),
    )


@router.get("/mods/{request_id}/files", response_model=FilePreviewResponse)
async def get_mod_files(request_id: str) -> FilePreviewResponse:
    """Get generated file preview for a request."""
    from storage.redis import get_pipeline_state

    redis_state = await get_pipeline_state(request_id)
    if redis_state:
        outputs = redis_state.get("outputs", {})
        files = {}
        for gen_name, gen_output in outputs.items():
            files.update(gen_output.get("files", {}))
        return FilePreviewResponse(request_id=request_id, files=files)

    output = await get_mod_output(request_id)
    if not output:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    files_preview = output.get("files_preview", [])
    return FilePreviewResponse(
        request_id=request_id,
        files={f: {} for f in files_preview},
    )


@router.get("/users/{user_id}/history", response_model=HistoryResponse)
async def get_history(
    user_id: str,
    _auth: Annotated[bool, Depends(verify_api_key)],
) -> HistoryResponse:
    """Get generation history for a user."""
    from app.config import get_config
    cfg = get_config()
    if not user_id or len(user_id) < 1:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not cfg.api_key:
        raise HTTPException(status_code=401, detail="Authentication required")
    if cfg.api_owner_user_id and user_id != cfg.api_owner_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: not authorized to access this user's history")
    entries = await get_user_history(user_id)
    return HistoryResponse(
        user_id=user_id,
        entries=[
            HistoryEntry(
                request_id=e["request_id"],
                prompt=e["prompt"],
                status=e["status"],
                created_at=(
                    e["created_at"].isoformat()
                    if isinstance(e["created_at"], datetime)
                    else str(e["created_at"])
                ),
            )
            for e in entries
        ],
    )


def _compute_progress(redis_state: dict) -> dict[str, int | str | None]:
    """Compute pipeline progress percentage and current stage from Redis state."""
    status = redis_state.get("status", "pending")
    stage_map: dict[str, tuple[str, int]] = {
        "pending": ("pending", 0),
        "routing": ("routing", 5),
        "generating": ("generating", 20),
        "t1_gating": ("validating", 60),
        "t2_gating": ("reviewing", 75),
        "packaging": ("packaging", 90),
        "done": ("completed", 100),
        "failed": ("failed", 100),
    }
    stage, percent = stage_map.get(status, ("unknown", 0))

    # Refine generating progress based on generator completion
    if status == "generating":
        succeeded = redis_state.get("generators_succeeded", [])
        failed = redis_state.get("generators_failed", [])
        total = len(succeeded) + len(failed) + 1  # +1 for current
        if total > 1:
            percent = 20 + int((len(succeeded) + len(failed)) / total * 35)

    return {"stage": stage, "percent": percent}