"""API routes — P1-impl with real PostgreSQL + Redis."""
import secrets
import structlog
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas import (
    ModStatusResponse,
    FilePreviewResponse,
    HistoryResponse,
    HistoryEntry,
)
from storage.queries import (
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


@router.get("/mods/{request_id}", response_model=ModStatusResponse)
async def get_mod_status(request_id: str) -> ModStatusResponse:
    """Get generation status and result for a request.
    
    Cache-first: check Redis first, then PostgreSQL.
    """
    from storage.redis import get_pipeline_state

    redis_state = await get_pipeline_state(request_id)
    if redis_state:
        logger.info("api.status.cache_hit", request_id=request_id)
        return ModStatusResponse(
            request_id=request_id,
            status=redis_state.get("status", "pending"),
            zip_url=redis_state.get("zip_key"),
            files_preview=[f for out in redis_state.get("outputs", {}).values() for f in out.get("files", {}).keys()],
            t1_errors=redis_state.get("errors", []),
            t2_feedback=redis_state.get("t2_feedback"),
            t2_score=redis_state.get("t2_score"),
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