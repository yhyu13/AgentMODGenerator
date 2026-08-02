"""API routes — P1-impl with real PostgreSQL + Redis."""
import asyncio
import hashlib
import json
import uuid
import secrets
import structlog
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response

from app.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    BatchGenerateRequest,
    BatchGenerateResponse,
    BatchGenerateItem,
    ModStatusResponse,
    FilePreviewResponse,
    HistoryResponse,
    HistoryEntry,
    CancellationReasonsListResponse,
    CancellationReasonResponse,
    ModMetadataResponse,
    ModSummaryResponse,
    TimelineStage,
    ModTimelineResponse,
    GeneratorInfo,
    GeneratorsResponse,
    PhaseInfo,
    PackInfo,
    PhasesResponse,
    KnownPhasesResponse,
    PacksResponse,
    RoutePreviewResponse,
    ModListItem,
    ModListResponse,
    StatsResponse,
    StatusBreakdown,
    PhaseBreakdown,
    FeatureFlagValue,
    FeatureFlagsResponse,
    FlagHistoryEntry,
    FlagHistoryResponse,
    FeatureFlagUpdate,
    FeatureFlagChangeResponse,
    FeatureFlagRollbackResponse,
    FeatureFlagPinResponse,
    FeatureFlagPinStateResponse,
    FeatureFlagPinSummary,
    FeatureFlagPinsResponse,
    T2JudgeIteration,
    T2JudgesResponse,
    PhaseEstimate,
    EstimatesResponse,
    PhaseEstimateResponse,
    PromptEstimateResponse,
    BatchPromptEstimateItem,
    BatchPromptEstimateRequest,
    BatchPromptEstimateResponse,
    PhaseDetailResponse,
    LogEntry,
    ModLogsResponse,
    PurgeRequest,
    PurgeResponse,
)
from storage.queries import (
    create_mod_request,
    get_mod_output,
    get_user_history,
    get_mod_request_stats,
    list_mod_requests,
    count_mod_requests,
    delete_old_mod_requests,
)

# Canonical set of cancellation reason ids. Mirrors
# ``storage.redis.KNOWN_CANCELLATION_REASONS`` from the
# discord-ops-hardening branch so clients can validate reason strings
# without scraping the source. Kept local to this module for the
# /v1/mods/cancellation_reasons endpoint; the parent session can
# relocate it to storage/redis.py when porting the rest of the
# cancellation_reason system (cancel writes, /cancellation_reason GET).
KNOWN_CANCELLATION_REASONS: frozenset[str] = frozenset({
    "user_cancelled",
    "timeout",
    "t2_failed",
    "t1_failed",
    "content_filter",
    "llm_error",
})

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


def _estimate_seconds(prompt: str) -> int:
    """Estimate generation time based on prompt keywords."""
    prompt_lower = prompt.lower()
    if any(k in prompt_lower for k in ("texture", "sprite", "image")):
        return 30
    if any(k in prompt_lower for k in ("npc", "schedule", "dialogue")):
        return 60
    if any(k in prompt_lower for k in ("farm expansion", "building", "warp", "map edit")):
        return 75
    return 90


@router.post("/mods/generate", response_model=GenerateResponse)
async def generate_mod(request: Request, req: GenerateRequest) -> GenerateResponse:
    """Start mod generation pipeline (non-blocking). Use /status/{id} to poll."""
    from orchestrator.pipeline import run_pipeline_background
    from storage.redis import set_status as redis_set_status
    from storage.queries import create_mod_request

    # Re-read the raw JSON body so downstream orchestrator code can access
    # it (e.g. the background pipeline may need fields beyond the
    # GenerateRequest schema, or want to log the original payload).
    # FastAPI/Starlette caches the body after the first read, so this is
    # safe to call even after the ``req: GenerateRequest`` parameter has
    # been parsed above.
    body_dict = await request.json()

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

    estimated = _estimate_seconds(req.prompt)
    return GenerateResponse(request_id=request_id, status="running", estimated_seconds=estimated)


@router.post("/mods/generate/batch", response_model=BatchGenerateResponse)
async def generate_mod_batch(req: BatchGenerateRequest) -> BatchGenerateResponse:
    """Start multiple mod generation pipelines in parallel."""
    from orchestrator.pipeline import run_pipeline_background
    from storage.redis import set_status as redis_set_status
    from storage.queries import create_mod_request

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    items: list[BatchGenerateItem] = []

    for prompt in req.prompts:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        logger.info(
            "api.generate_batch.start",
            batch_id=batch_id,
            request_id=request_id,
            user_id=req.user_id,
            prompt=prompt,
        )
        await create_mod_request(request_id, req.user_id, prompt, "batch", [], {})
        await redis_set_status(request_id, "running")
        run_pipeline_background(request_id, req.user_id, prompt)
        items.append(BatchGenerateItem(
            prompt=prompt,
            request_id=request_id,
            status="running",
            estimated_seconds=_estimate_seconds(prompt),
        ))

    logger.info("api.generate_batch.done", batch_id=batch_id, item_count=len(items))
    return BatchGenerateResponse(batch_id=batch_id, items=items)


@router.post(
    "/mods/purge",
    response_model=PurgeResponse,
    dependencies=[Depends(verify_api_key)],
)
async def purge_old_mods(body: PurgeRequest) -> PurgeResponse:
    """Bulk-delete old mod requests and their Redis state.

    v106 Blue (Feature 4 — purge_old_mods admin command; companion
    to the v104 ``PurgeRequest`` + ``PurgeResponse`` Pydantic models
    and the v105 ``delete_old_mod_requests`` SQL helper). Three
    layers of guard, evaluated in this order:

    1. ``Depends(verify_api_key)`` enforces the ``X-API-Key`` header
       when one is configured (the same gate used by
       ``GET /v1/users/{id}/history``).
    2. ``ADMIN_PURGE_ENABLED`` env var — the destructiveness of this
       endpoint means it must only be available when the operator
       has explicitly opted in via the env var (default ``False``).
       The flag is read from ``cfg.admin_purge_enabled`` (the
       ``Config`` dataclass field added in v107 — promoted from the
       inline ``os.getenv(...)`` that v106 used, so the parsing
       vocabulary now lives in one place alongside the other
       ``Config`` fields). When the flag is off, we return
       ``403 Forbidden`` with a clear detail message so a
       misconfigured client sees why the call failed rather than
       getting a silent no-op.
    3. Pydantic-validated ``body.days`` (``1 <= days <= 365``) —
       bad values are rejected with 422 BEFORE this handler runs,
       so the SQL helper's ``days < 1`` short-circuit is the
       defence-in-depth for internal callers, not a primary guard.

    On success the endpoint deletes every ``mod_requests`` row
    older than ``body.days`` and best-effort removes the matching
    Redis keys for each id. Redis errors are logged at WARNING and
    swallowed — the SQL row is the source of truth, and a stale
    Redis key will simply expire on its normal TTL. The Redis
    cleanup loop also absorbs ``ImportError`` gracefully: if the
    three ``storage.redis.delete_*`` cleanup helpers are not yet
    present on master (they live on the discord-ops-hardening
    branch), the SQL purge still completes and the absence is
    logged once at WARNING so operators know the Redis keys will
    TTL out on their own.

    **Route ordering.** Registered BEFORE ``/mods/status/{request_id}``
    and BEFORE the generic ``/mods/{request_id}`` pattern so
    FastAPI's path matcher resolves the static ``/mods/purge``
    segment first. The same defensive ordering used by
    ``/mods/stats``, ``/mods/cancel/{request_id}``, and
    ``/mods/{request_id}/retry`` elsewhere in this module.

    Args:
        body: :class:`PurgeRequest` carrying the ``days`` window
            (``1..365``). Out-of-range values are rejected with
            422 by Pydantic before this function runs.

    Returns:
        PurgeResponse: ``days`` echoed back, plus ``deleted_count``
        and a sample of up to 50 ``deleted_request_ids`` for audit
        (the full list can be inferred from ``deleted_count`` —
        capping the response envelope prevents a multi-thousand-row
        purge from blowing the response body).

    Emits:
        ``api.purge.disabled`` (WARNING) when ``ADMIN_PURGE_ENABLED``
        is off. ``api.purge.redis_cleanup_done`` (INFO) after the
        Redis cleanup loop. ``api.purge.redis_cleanup_failed``
        (WARNING) per Redis helper error.
        ``api.purge.redis_helpers_missing`` (WARNING) once if the
        three ``storage.redis.delete_*`` helpers are not yet on
        master. ``api.purge.done`` (INFO) on success.
    """
    # Guard 2 — admin env gate. Read via ``cfg.admin_purge_enabled``
    # so the flag is part of the ``Config`` dataclass (v107 promoted
    # the inline ``os.getenv`` to ``app/config.py`` for consistency
    # with the other ``Config`` fields). The default ``False`` plus
    # truthy parser (``1`` / ``true`` / ``yes`` case-insensitive)
    # is identical to the v106 inline behavior, so existing callers
    # and the v106 test matrix (``test_purge_disabled_falsy_strings``,
    # ``test_purge_enabled_truthy_strings``) continue to pass without
    # code changes — only the env-var source moves from
    # ``os.getenv`` to the singleton ``Config`` instance.
    from app.config import get_config

    cfg = get_config()
    if not cfg.admin_purge_enabled:
        logger.warning(
            "api.purge.disabled",
            days=body.days,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Admin purge is disabled. Set ADMIN_PURGE_ENABLED=true "
                "to enable this endpoint."
            ),
        )

    deleted_ids = await delete_old_mod_requests(days=body.days)

    # Best-effort Redis cleanup. A transient Redis error here is
    # non-fatal — the SQL row is the source of truth, and a stale
    # Redis key will expire on its own TTL. We mirror the
    # cancel-reason graceful-degrade pattern from cancel_mod (v45).
    if deleted_ids:
        await _cleanup_redis_for_purge(deleted_ids)

    logger.info(
        "api.purge.done",
        days=body.days,
        deleted_count=len(deleted_ids),
    )
    # Surface a sample (not the full list) of ids in the response.
    # A multi-thousand-row purge would blow the response envelope;
    # operators can still see the true count via deleted_count.
    sample_size = min(50, len(deleted_ids))
    return PurgeResponse(
        days=body.days,
        deleted_count=len(deleted_ids),
        deleted_request_ids=deleted_ids[:sample_size],
    )


async def _cleanup_redis_for_purge(deleted_ids: list[str]) -> None:
    """Best-effort Redis cleanup for ``purge_old_mods``.

    v106 Blue — extracted from the main handler so Pyright sees
    a clean scope for the deferred-import. The three
    ``storage.redis.delete_*`` helpers live on the
    discord-ops-hardening branch and may not yet be on master;
    if they're missing we log once at WARNING and let the SQL
    row deletion stand as the source of truth (the stale Redis
    keys will TTL out on their own).
    """
    try:
        from storage.redis import (
            delete_pipeline_state,
            delete_cancellation_reason,
            delete_notification_target,
        )
    except ImportError:
        logger.warning(
            "api.purge.redis_helpers_missing",
            ids_count=len(deleted_ids),
        )
        return

    redis_cleaned = 0
    for rid in deleted_ids:
        for helper in (
            delete_pipeline_state,
            delete_cancellation_reason,
            delete_notification_target,
        ):
            try:
                await helper(rid)
                redis_cleaned += 1
            except (
                ConnectionError,
                asyncio.TimeoutError,
                RuntimeError,
            ) as exc:
                logger.warning(
                    "api.purge.redis_cleanup_failed",
                    request_id=rid,
                    helper=helper.__name__,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
    logger.info(
        "api.purge.redis_cleanup_done",
        ids_count=len(deleted_ids),
        redis_cleaned=redis_cleaned,
    )


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


@router.post("/mods/{request_id}/retry")
async def retry_mod(
    request_id: str,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Response:
    """Replay a failed/cancelled mod request under a fresh request_id.

    v53 Blue (Session 3 final sub-resource): three guards in order —
    (1) ``RETRY_ENABLED`` env gate (test/dev defaults to off), (2)
    per-user retry counter capped at ``RETRY_MAX_PER_USER_PER_DAY``
    (default 5) per 24h, (3) original status must be in
    ``{failed, cancelled, error}``. The state validation happens
    AFTER the counter check so a retry of a `done` request returns
    409 BEFORE consuming a counter slot (the documented ordering).
    Original request's prompt + user_id come from ``get_mod_output``
    (Postgres source of truth) so the user-isolation check survives
    Redis eviction.

    **Adaptation for master's pipeline signature:** the source
    bundle's call site passes ``run_pipeline_background(new_id,
    user_id, prompt, [], with_rewards=False)``, but master's
    ``orchestrator.pipeline.run_pipeline_background`` only accepts
    ``(request_id, user_id, prompt)``. The retry endpoint drops the
    unsupported ``generators=[]`` and ``with_rewards=False`` args
    — the with_rewards default for the original ``generate_mod``
    endpoint is also off, so the retry matches the default-cold
    path. If/when master upgrades the pipeline signature, the
    retry endpoint can be enriched to forward the args.

    Route ordering: registered BEFORE ``/mods/status/{request_id}``
    so FastAPI's path matcher resolves the static ``/retry``
    suffix ahead of the generic ``{request_id}`` parameter route
    (the same defensive pattern used by
    ``/mods/cancel/{request_id}``).
    """
    from app.config import get_config

    cfg = get_config()

    # Guard 1 — env gate. Default ``false`` in test/dev; the
    # ``Config.retry_enabled`` dataclass field is parsed from the
    # ``RETRY_ENABLED`` env var via the same truthy parser as
    # ``Config.admin_purge_enabled`` (``1`` / ``true`` / ``yes``
    # case-insensitive, see ``app/config.py``). v108 promoted the
    # parse out of the handler so the operator-facing vocabulary
    # is uniform across admin gates.
    if not cfg.retry_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retry endpoint is disabled (RETRY_ENABLED != true)",
        )

    # Guard 0 — auth header. The retry endpoint has no body,
    # so the caller's identity MUST come from the
    # ``X-User-ID`` request header. Missing header → 401 so
    # the client learns the auth posture (vs the 404 they
    # get from the auth-mismatch path further down).
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-ID header",
        )

    # Guard 2 — per-user retry counter. Decrement FIRST so the
    # race-safe restoration (``incr`` on negative) is the only
    # path that returns 429. The TTL is set on the FIRST
    # decrement of the day (``remaining == max - 1``).
    from storage.redis import get_client as _get_redis

    redis = await _get_redis()
    counter_key = f"retry_counter:{x_user_id}"
    # ``Config.retry_max_per_user_per_day`` is parsed via ``_safe_int``
    # so malformed env values (non-numeric, floats, empty string)
    # fall back to the configured default — same graceful-degrade
    # pattern as ``zip_output_timeout``. v108 supersedes the v53
    # inline ``try/except ValueError -> 5`` fallback in the handler.
    max_retries = cfg.retry_max_per_user_per_day
    remaining = await redis.decr(counter_key)
    if remaining < 0:
        # Race-safe restoration — restore the counter to 0 so
        # the next request within the window still observes
        # the exhausted state.
        await redis.incr(counter_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Retry limit exceeded for this user",
        )
    if remaining == max_retries - 1:
        # First decrement of the day — anchor the 24h window
        # by setting the TTL on the counter key.
        await redis.expire(counter_key, 86400)

    # Guard 3 — look up the original request. Redis-first per
    # the documented contract; Postgres fallback so the
    # user-isolation check (which reads ``user_id``) survives
    # Redis eviction.
    from storage.redis import get_pipeline_state
    from storage.redis import set_status as redis_set_status

    original_user_id: str | None = None
    original_prompt: str | None = None
    original_status: str | None = None

    redis_state = await get_pipeline_state(request_id)
    if redis_state:
        original_user_id = redis_state.get("user_id")
        original_prompt = redis_state.get("prompt")
        original_status = redis_state.get("status")

    if original_user_id is None or original_prompt is None or original_status is None:
        # Cache miss — fall through to Postgres (the canonical
        # source of truth for user_id + prompt + status).
        original_row = await get_mod_output(request_id)
        if not original_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mod request {request_id} not found",
            )
        original_user_id = original_row.get("user_id")
        original_prompt = original_row.get("prompt")
        original_status = original_row.get("status")

    # Pyright narrowing — after the fall-through above the
    # three locals MUST be strings (Postgres ``mod_requests``
    # is the source of truth, and the schema enforces NOT NULL
    # on user_id + prompt + status). If the row is somehow
    # missing any of those columns (corrupted row), surface a
    # 404 so the client sees a clean error instead of an
    # internal 500 from the downstream ``create_mod_request``
    # call.
    if (
        not isinstance(original_user_id, str)
        or not isinstance(original_prompt, str)
        or not isinstance(original_status, str)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mod request {request_id} not found",
        )

    # Auth isolation — return 404 (NOT 403) so a non-owner
    # cannot enumerate which request_ids belong to alice. The
    # ``original_user_id`` from Redis is the client-supplied
    # string in the request body, so the comparison must be a
    # literal ``==`` (NOT case-folded) — the DB stores what
    # the client sent on the original ``POST /v1/mods/generate``
    # call.
    if original_user_id != x_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mod request {request_id} not found",
        )

    # State validation — only ``failed``, ``cancelled``, and
    # ``error`` are retryable. ``done`` returns 409 because
    # the original succeeded (the client should poll the
    # original); ``running`` returns 409 because the original
    # is still in flight (no point spinning up a duplicate).
    if original_status not in {"failed", "cancelled", "error"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry: status={original_status}",
        )

    # Mint a fresh request_id. Use the ``req_<12 hex>`` prefix
    # to match the convention used by ``generate_mod`` and
    # ``generate_mod_batch`` so the new request is
    # indistinguishable from any other fresh request
    # downstream of the dispatch.
    new_request_id = f"req_{uuid.uuid4().hex[:12]}"
    logger.info(
        "api.retry.start",
        original_request_id=request_id,
        new_request_id=new_request_id,
        user_id=x_user_id,
        original_status=original_status,
    )

    # Re-submit the pipeline under the fresh request_id. Same
    # channel + tag defaults as ``generate_mod`` so the retry
    # is indistinguishable from any other first-time request
    # in the audit log / DB. ``create_mod_request`` signature
    # on master is ``(request_id, user_id, prompt, phase,
    # generators, hint)`` — phase is hardcoded to
    # ``"p1_shop_channel"`` to match the original generate_mod
    # default; the retry path does not know the original
    # phase (Redis state may have lost it on eviction) so the
    # safe default is the same channel ``generate_mod``
    # always picks.
    await create_mod_request(
        new_request_id, x_user_id, original_prompt, "p1_shop_channel", [], {},
    )
    await redis_set_status(new_request_id, "running")
    # Adapted to master's 3-arg ``run_pipeline_background``
    # signature (see docstring). ``with_rewards=False`` because
    # the retry endpoint has no way to know the original
    # caller's preference — the safe default is the cheaper
    # path.
    from orchestrator.pipeline import run_pipeline_background

    run_pipeline_background(new_request_id, x_user_id, original_prompt)

    logger.info(
        "api.retry.done",
        original_request_id=request_id,
        new_request_id=new_request_id,
        user_id=x_user_id,
    )
    return JSONResponse(
        content=GenerateResponse(
            request_id=new_request_id,
            status="running",
        ).model_dump()
    )


@router.post("/mods/cancel/{request_id}")
async def cancel_mod(request_id: str) -> dict:
    """Cancel a running mod generation request.

    Records the cancellation reason (``"user_cancelled"``) so the
    reason is preserved across later status overwrites and is
    queryable via ``GET /v1/mods/{id}/cancellation_reason``. A failure
    to write the reason key does not abort the cancel — the user's
    intent (stop the request) is honored either way, and the
    reasonless-cancel case is a graceful fallback for older clients.
    """
    from storage.redis import set_status as redis_set_status, get_pipeline_state
    from storage.redis import set_cancellation_reason

    state = await get_pipeline_state(request_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    current_status = state.get("status", "unknown")
    if current_status in ("done", "failed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel request with status: {current_status}",
        )

    await redis_set_status(request_id, "cancelled")
    # Real cancellation — stop the running pipeline task, not just the
    # status key. Before the request_id → Task registry existed, the
    # pipeline kept running to completion (LLM quota, S3 writes, and a
    # final "done" DM for a request the user cancelled).
    from orchestrator.pipeline import cancel_pipeline_task

    task_cancelled = cancel_pipeline_task(request_id)
    if not task_cancelled:
        logger.info(
            "api.cancel.no_task",
            request_id=request_id,
            previous_status=current_status,
        )
    # Write the reason. The literal "user_cancelled" is the value we
    # want recorded — assigning `await set_cancellation_reason(...)`
    # (which returns None, because set_cancellation_reason is a writer)
    # would always produce a null ``cancellation_reason`` in the
    # response payload. The branch source has this small bug; we
    # assign the string literal directly so the response surfaces
    # the recorded reason.
    reason: str | None = "user_cancelled"
    try:
        await set_cancellation_reason(request_id, reason)
    except (ValueError, ConnectionError, RuntimeError, OSError) as exc:
        # Surface the failure in logs but don't fail the request — the
        # user wants the request cancelled, and the cancel still
        # succeeded. The reason key can be back-filled later if
        # needed. Narrow catch: a programming bug (TypeError, KeyError)
        # should still surface so it isn't masked as a transient
        # outage.
        logger.warning(
            "api.cancel.reason_unrecorded",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        reason = None
    logger.info(
        "api.cancel.done",
        request_id=request_id,
        previous_status=current_status,
        cancellation_reason=reason,
    )
    return {
        "request_id": request_id,
        "status": "cancelled",
        "previous_status": current_status,
        "cancellation_reason": reason,
    }


@router.get("/mods/cancellation_reasons", response_model=CancellationReasonsListResponse)
async def list_cancellation_reasons() -> CancellationReasonsListResponse:
    """Return the canonical list of valid cancellation reason ids.

    Read-side companion of ``GET /v1/mods/{id}/cancellation_reason``.
    Returns the sorted, deduplicated set of cancellation reason ids
    that may appear on a :class:`ModStatusResponse.cancellation_reason`
    field — mirrors ``storage.redis.KNOWN_CANCELLATION_REASONS`` so
    clients can validate a reason string without scraping the source.

    The list is small and stable (a few enum-like strings) so this
    endpoint is cheap; the response is also useful for chat bots
    rendering a "why was my request cancelled?" picker.

    This endpoint is the documented counterpart of the comment in
    ``storage/redis.py`` that says "if a new reason is added, update
    KNOWN_REASONS at the same time the writer is added so
    /v1/mods/cancellation_reasons stays in sync" — without this
    endpoint, the comment was a forward reference to nothing.

    Note: registered BEFORE ``/mods/{request_id}`` because FastAPI's
    path matching is declaration-order sensitive — a request to
    ``/v1/mods/cancellation_reasons`` would otherwise be captured by
    the generic ``{request_id}`` route. Same defensive ordering used by
    ``/mods/stats`` and ``/mods/generators`` elsewhere in this file.
    """
    reasons = sorted(KNOWN_CANCELLATION_REASONS)
    logger.info("api.cancellation_reasons.listed", count=len(reasons))
    return CancellationReasonsListResponse(reasons=reasons, count=len(reasons))


@router.get(
    "/mods/{request_id}/cancellation_reason",
    response_model=CancellationReasonResponse,
)
async def get_cancellation_reason_endpoint(request_id: str) -> CancellationReasonResponse:
    """Get the cancellation reason for a cancelled mod request.

    Returns the stored reason (one of
    ``storage.redis.KNOWN_CANCELLATION_REASONS``) for a request whose
    status is ``cancelled``. Useful for chat bots and dashboards that
    want to tell the user *why* their request was cancelled without
    paying the cost of the full status payload.

    Returns 404 if the request is unknown, 400 if the request is not
    cancelled (because "cancellation reason" is meaningless for
    non-cancelled requests — the ``null`` field is reserved for
    cancellations that pre-date the reason-key feature, not for
    in-flight requests).
    """
    from storage.redis import get_cancellation_reason, get_status

    status_value = await get_status(request_id)
    if status_value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )
    if status_value != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Request {request_id} is not cancelled "
                f"(current status: {status_value})"
            ),
        )
    # Narrow catch on the reason lookup: a transient Redis error
    # shouldn't crash the whole endpoint — we can still surface the
    # status. A programming bug (TypeError, AttributeError) should
    # still propagate so it isn't masked as a transient outage.
    reason: str | None = None
    try:
        reason = await get_cancellation_reason(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.cancellation_reason.lookup_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return CancellationReasonResponse(
        request_id=request_id,
        status="cancelled",
        cancellation_reason=reason,
    )


@router.get("/mods/generators", response_model=GeneratorsResponse)
async def list_generators(game: str, phase: str) -> GeneratorsResponse:
    """List the generators available for a specific (game, phase) pair.

    Returns the generators for that phase, each tagged with its 0-based
    position in the phase's execution order. Read-only: no DB / Redis
    state, no side effects — this is a static introspection endpoint
    over the registered :class:`GamePack` registry.

    Args:
        game: Game pack id (e.g. ``stardew_valley``).
        phase: Phase within the pack (e.g. ``shop_channel``).

    Raises:
        404 if the game pack is unknown or the phase does not exist in it.

    Note: registered BEFORE ``/mods/{request_id}`` because FastAPI's
    path matching is declaration-order sensitive — a request to
    ``/v1/mods/generators`` would otherwise be captured by the generic
    ``{request_id}`` route. Same defensive ordering used by
    ``/mods/stats`` and ``/mods/cancellation_reasons`` above.
    """
    from generators.core import get_game_pack

    pack = get_game_pack(game)
    if pack is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown game pack: {game}",
        )
    if phase not in pack.list_phases():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phase {phase!r} not found in pack {game!r}",
        )
    try:
        pg = pack.get_generators(phase)
        execution_order = list(pg.execution_order)
    except ValueError as exc:
        logger.warning(
            "api.generators.phase_lookup_failed",
            game=game,
            phase=phase,
            error=str(exc), error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phase {phase!r} not available in pack {game!r}",
        ) from exc

    generators: list[GeneratorInfo] = [
        GeneratorInfo(
            name=name,
            phase=phase,
            game=game,
            execution_position=idx,
        )
        for idx, name in enumerate(execution_order)
    ]

    logger.info(
        "api.generators.listed",
        game=game,
        phase=phase,
        count=len(generators),
    )
    return GeneratorsResponse(game=game, phase=phase, generators=generators)


@router.get("/mods/phases", response_model=PhasesResponse)
async def list_phases() -> PhasesResponse:
    """List all registered game packs and their available phases.

    Returns one entry per registered pack, with each pack listing its
    phases and the generator names (in execution order) for each phase.
    Replaces the previous untyped ``{"phases": {phase: [generators]}}``
    shape with a typed schema that distinguishes between packs.

    The top-level ``phases`` field is the sorted union of every phase
    across all packs — the canonical list clients should use to validate
    a ``phase`` parameter before calling ``POST /v1/mods/generate``.

    Read-only: no DB / Redis state, no side effects — purely a static
    introspection endpoint over the registered :class:`GamePack`
    registry. Defensive against any pack that exposes
    ``list_phases()`` but cannot resolve a phase's generators (e.g. an
    empty ``get_generators()`` raising ``ValueError``) — those phases
    show up with ``generator_count=0`` and an empty ``execution_order``
    rather than producing a 500.

    Note: registered BEFORE ``/mods/{request_id}`` because FastAPI's
    path matching is declaration-order sensitive — same defensive
    ordering used by ``/mods/stats``, ``/mods/generators`` and
    ``/mods/cancellation_reasons``.
    """
    from generators.core import list_game_packs, get_game_pack

    packs: list[PackInfo] = []
    flat_phases: set[str] = set()
    for pack_id in list_game_packs():
        pack = get_game_pack(pack_id)
        if pack is None:
            logger.warning("api.phases.pack_missing", pack_id=pack_id)
            continue
        manifest = pack.get_manifest()
        phase_infos: list[PhaseInfo] = []
        for phase in pack.list_phases():
            try:
                pg = pack.get_generators(phase)
                execution_order = list(pg.execution_order)
            except ValueError:
                # Defensive: an otherwise-valid pack that fails to
                # resolve a phase gets an empty execution_order
                # rather than a 500 — same pattern the
                # ``/v1/mods/generators`` defensive try/except uses.
                execution_order = []
            phase_infos.append(
                PhaseInfo(
                    phase=phase,
                    generator_count=len(execution_order),
                    execution_order=execution_order,
                )
            )
            flat_phases.add(phase)
        packs.append(
            PackInfo(
                game_id=manifest.game_id,
                display_name=manifest.display_name,
                mod_format=manifest.mod_format,
                phases=phase_infos,
            )
        )

    known = sorted(flat_phases)
    logger.info(
        "api.phases.listed",
        pack_count=len(packs),
        phase_count=len(known),
    )
    return PhasesResponse(packs=packs, phases=known)


@router.get("/mods/phases/known", response_model=KnownPhasesResponse)
async def list_known_phases() -> KnownPhasesResponse:
    """Return the canonical list of known phase ids.

    Thin alias for the ``phases`` field of ``GET /v1/mods/phases``,
    exposed as its own endpoint so clients that only need the flat
    list (e.g. to validate a ``phase`` parameter before calling
    ``POST /v1/mods/generate``, or to populate a dropdown in a UI)
    can do so without paying the per-pack serialization cost of the
    full ``/v1/mods/phases`` table.

    Returns the sorted, deduplicated union of every phase id across
    all registered packs — same data ``PhasesResponse.phases``
    exposes, but without the per-pack breakdown. The ``count`` field
    is the length of the flat list (== ``len(phases)``), matching
    the convention :class:`CancellationReasonsListResponse` uses
    for its ``reasons`` / ``count`` pair.

    Read-only: no DB / Redis state, no side effects — purely a
    static introspection endpoint over the registered
    :class:`GamePack` registry. Defensive against any pack that
    exposes ``list_phases()`` but cannot resolve a phase's
    generators (the ``list_phases`` defensive ``ValueError``
    pattern is not exercised here because this handler does NOT
    call ``get_generators`` — it only walks ``pack.list_phases()``).

    No new imports at module top: ``generators.core`` is deferred
    into the handler body to avoid pulling the entire import graph
    at route-module import time (same convention as
    ``list_phases`` and ``list_generators``).

    Note: registered BEFORE ``/mods/{request_id}`` because FastAPI's
    path matching is declaration-order sensitive — same defensive
    ordering used by ``/mods/phases``, ``/mods/generators`` and
    ``/mods/cancellation_reasons``.
    """
    from generators.core import list_game_packs, get_game_pack

    flat_phases: set[str] = set()
    for pack_id in list_game_packs():
        pack = get_game_pack(pack_id)
        if pack is None:
            # Same defensive skip ``list_phases`` uses — a pack id
            # advertised by ``list_game_packs()`` but not resolvable
            # via ``get_game_pack()`` is silently skipped rather
            # than raising.
            logger.warning("api.phases.known.pack_missing", pack_id=pack_id)
            continue
        for phase in pack.list_phases():
            flat_phases.add(phase)

    phases = sorted(flat_phases)
    logger.info("api.phases.known.listed", count=len(phases))
    return KnownPhasesResponse(phases=phases, count=len(phases))


@router.get("/mods/phases/{phase_id}", response_model=PhaseDetailResponse)
async def get_phase_detail(phase_id: str) -> PhaseDetailResponse:
    """Return the full detail envelope for a single phase id.

    Additive alongside the existing ``GET /v1/mods/phases`` (which lists
    every pack + phase) and ``GET /v1/mods/phases/known`` (which returns
    the flat phase list). Lets a caller fetch the *detail* for one phase
    id without parsing the full table — same pattern as
    ``GET /v1/estimates/{phase}`` does for the estimate table.

    Lookup algorithm: walk every registered pack, ask each one whether
    the requested phase is in ``list_phases()``, and stop at the first
    hit. The router's longest-keyword-wins tiebreak is not relevant
    here — phases live inside packs and two packs exporting the same id
    is a registry-design error, not a routing decision.

    Args:
        phase_id: Phase id (e.g. ``shop_channel``, ``weather_event``).
            Echoed back in the response. An unknown phase is NOT a
            404 — instead the endpoint returns ``matched=False`` with
            empty owning-pack fields and ``generator_count=0`` so chat
            bots can degrade gracefully ("this phase is not
            registered") without a try/except on the client. Mirrors
            :func:`get_estimate_for_phase` which uses the same
            ``matched=False`` graceful-degrade shape.

    Returns:
        The single-phase envelope. ``matched`` is True iff the phase
        id was found in at least one registered pack's
        ``list_phases()``. ``execution_order`` is the canonical
        generator pipeline for the phase (empty list on miss or when
        the pack cannot resolve the phase). ``estimated_seconds``
        comes from :func:`app.estimation.estimate_seconds_for_phase`;
        the ``default_seconds`` echo mirrors the same field on
        :class:`PhaseEstimateResponse`.
    """
    from app.estimation import _DEFAULT_SECONDS, estimate_seconds_for_phase
    from generators.core import get_game_pack, list_game_packs

    # Defensive trim: FastAPI's path param rejects empty strings at
    # the routing layer, but a whitespace-only phase (e.g.
    # ``/v1/mods/phases/%20%20``) could still slip through. Treat
    # it the same as an unknown phase so the response shape is
    # consistent — ``matched=False`` is the canonical signal for
    # "not registered". Mirrors ``get_estimate_for_phase`` which
    # uses the same defensive trim.
    cleaned_phase = phase_id.strip()
    if not cleaned_phase:
        cleaned_phase = ""

    # Search every registered pack for one whose supported phases
    # include ``cleaned_phase``. We can't route by phase alone —
    # phases live inside packs, and the same phase id could in
    # theory appear in two packs (the router's
    # longest-keyword-wins tiebreak handles that, but here we
    # just accept the first hit). Keeping the lookup pack-agnostic
    # means the endpoint keeps working when new packs register
    # without any change to this handler.
    matched_game_id = ""
    matched_display_name = ""
    matched_mod_format = ""
    execution_order: list[str] = []
    matched = False

    if cleaned_phase:
        for pack_id in list_game_packs():
            pack_cls = get_game_pack(pack_id)
            if pack_cls is None:
                continue
            try:
                phase_list = pack_cls.list_phases()
            except (NotImplementedError, AttributeError):
                # A pack that hasn't implemented list_phases() or
                # is missing the method is silently skipped.
                continue
            if cleaned_phase not in phase_list:
                continue

            # First-hit wins. Resolve the manifest + execution
            # order for the owning pack and stop the walk.
            try:
                manifest = pack_cls.get_manifest()
            except (NotImplementedError, AttributeError):
                # Defensive: a pack that exposes phases but cannot
                # return a manifest is treated as "not registered"
                # so the caller still gets a well-formed envelope.
                break
            try:
                pg = pack_cls.get_generators(cleaned_phase)
                execution_order = list(pg.execution_order)
            except (NotImplementedError, ValueError, AttributeError):
                # Same defensive default as ``GET /v1/mods/phases``:
                # unknown phases within an otherwise-valid pack
                # produce an empty execution_order rather than a
                # 500. We still flag ``matched=True`` because the
                # pack does list the phase.
                execution_order = []
            matched_game_id = str(getattr(manifest, "game_id", "") or "")
            matched_display_name = str(getattr(manifest, "display_name", "") or "")
            matched_mod_format = str(getattr(manifest, "mod_format", "") or "")
            matched = True
            break

    seconds = int(estimate_seconds_for_phase(cleaned_phase or None))
    default = int(_DEFAULT_SECONDS)
    logger.info(
        "api.phases.detail_lookup",
        phase=cleaned_phase,
        matched=matched,
        game_id=matched_game_id,
        generator_count=len(execution_order),
        seconds=seconds,
    )
    return PhaseDetailResponse(
        phase=cleaned_phase,
        matched=matched,
        game_id=matched_game_id,
        display_name=matched_display_name,
        mod_format=matched_mod_format,
        generator_count=len(execution_order),
        execution_order=execution_order,
        estimated_seconds=seconds,
        default_seconds=default,
    )


@router.get("/packs", response_model=PacksResponse)
async def list_packs() -> PacksResponse:
    """Return the list of registered game packs.

    Thin alias for the ``packs`` field of ``GET /v1/mods/phases``,
    exposed as its own endpoint so clients that only need the pack
    registry (a web UI showing "this server supports the following N
    packs", an integration test that wants to assert which packs
    registered, a Discord bot populating a ``/pack-info``
    autocomplete) can do so without paying the per-phase
    serialization cost of ``GET /v1/mods/phases``.

    Mirrors the existing ``GET /v1/mods/phases/known`` endpoint which
    exposes the ``phases`` field of :func:`list_phases` as its own
    endpoint. Together with ``/v1/mods/phases/known`` and
    ``/v1/mods/phases/{phase_id}`` this completes the read-only
    phase / pack registry family — three small endpoints that return
    the parts of the full ``/v1/mods/phases`` table individually.

    The endpoint does NOT take any query parameters and does NOT
    require an API key — same convention as :func:`list_phases` and
    :func:`list_known_phases`. No new imports at module top (per
    AGENTS.md test isolation); ``generators.core`` is deferred into
    the handler body.

    Read-only: no DB / Redis state, no side effects — purely a
    static introspection endpoint over the registered :class:`GamePack`
    registry. Defensive against any pack that exposes
    ``list_phases()`` but cannot resolve a phase's generators (the
    same ``ValueError`` skip pattern that :func:`list_phases` uses):
    those phases surface with ``generator_count=0`` and an empty
    ``execution_order`` rather than producing a 500.

    Note: registered BEFORE ``/feature_flags`` (and before any
    ``/mods/{request_id}`` siblings) so FastAPI's path matching
    treats ``/packs`` as a static path — declaration-order matches
    the source bundle (packs comes right after the listing of known
    phases).

    Adapted from the discord-ops-hardening branch's ``list_packs``
    handler (source bundle line 2793-2856). The handler body is
    byte-identical to the branch's contract — same deferred imports,
    same defensive ``ValueError`` skip, same ``api.packs.listed``
    log event. The only divergences are docstring scope (this
    handler omits the cross-reference to ``/v1/mods/phases/{phase_id}``
    because that endpoint is not yet on master) and the
    :class:`PacksResponse` envelope shape, which lives in
    ``app/api/schemas.py`` (added in v37, this round).
    """
    from generators.core import get_game_pack, list_game_packs

    packs: list[PackInfo] = []
    for pack_id in list_game_packs():
        pack = get_game_pack(pack_id)
        if pack is None:
            logger.warning("api.packs.missing", pack_id=pack_id)
            continue
        manifest = pack.get_manifest()
        phase_infos: list[PhaseInfo] = []
        for phase in pack.list_phases():
            try:
                pg = pack.get_generators(phase)
                execution_order = list(pg.execution_order)
            except ValueError:
                # Defensive: an otherwise-valid pack that fails to
                # resolve a phase gets an empty execution_order
                # rather than a 500 — mirrors ``list_phases``.
                execution_order = []
            phase_infos.append(
                PhaseInfo(
                    phase=phase,
                    generator_count=len(execution_order),
                    execution_order=execution_order,
                )
            )
        packs.append(
            PackInfo(
                game_id=manifest.game_id,
                display_name=manifest.display_name,
                mod_format=manifest.mod_format,
                phases=phase_infos,
            )
        )

    logger.info("api.packs.listed", count=len(packs))
    return PacksResponse(packs=packs, count=len(packs))


@router.get("/route_preview", response_model=RoutePreviewResponse)
async def preview_route(
    prompt: Annotated[
        str,
        Query(
            min_length=1,
            description=(
                "The natural-language prompt the router would be asked "
                "to route. Required, non-empty (whitespace-only is "
                "rejected by the handler with a 422 — see Raises)."
            ),
        ),
    ],
    locales: Annotated[
        str | None,
        Query(
            description=(
                "v38 first cut: optional comma-separated locale codes "
                "to echo back in the response after split + dedup. "
                "The handler does NOT validate the BCP-47 shape (the "
                "branch's ``_validate_locales_field`` helper was "
                "deemed out of scope for v38 — see v39+ follow-up). "
                "Empty / whitespace-only string is treated as \"not "
                "provided\" so the zero-cost path holds."
            ),
        ),
    ] = None,
) -> RoutePreviewResponse:
    """Dry-run the prompt router without starting a generation.

    Lets clients (chat bots, web UIs, integration tests) preview
    which game + phase + generator pipeline the orchestrator would
    select for a given prompt *before* calling
    ``POST /v1/mods/generate``. The router is pure CPU — no LLM
    call, no DB write, no background task — so this endpoint is
    cheap and safe to call as often as the user types.

    Args:
        prompt: The natural-language prompt the router would be
            asked to route. Required, non-empty (whitespace-only
            rejected with a 422 below).
        locales: v38 first cut: optional comma-separated locale
            codes to echo back in the response. The v38 handler
            does NOT validate the BCP-47 shape — it splits on
            ``,`` and dedupes, then echoes the resulting list.
            Empty / whitespace-only string is treated as "not
            provided" so the zero-cost path holds. A v39+ follow-up
            will port the branch's ``_validate_locales_field``
            helper so invalid locale codes raise a 422 before the
            response is built (mirroring the branch's v52 Red
            behaviour).

    Returns:
        The resolved game, phase, generator execution order, plus
        the router's confidence score and the keyword that
        triggered the match (empty string when no keyword matched).
        When ``locales`` was provided, the split + deduped list
        is included in the response so callers can confirm what
        the server accepted.

    Raises:
        422 if ``prompt`` is empty or whitespace-only (the
        handler's defensive trim catches whitespace-only that
        :func:`Query`'s ``min_length=1`` cannot reject).

    Adapted from the discord-ops-hardening branch's
    ``preview_route`` (source bundle lines 2859-2947). The
    handler body is byte-identical to the branch's contract
    except for the v38 first-cut decision to skip
    ``_validate_locales_field`` (the branch's helper validates
    BCP-47 shape + the 8-cap; master's v38 handler splits and
    dedupes but does not validate). Adding that validator is a
    v39+ follow-up that ports the helper from the source bundle.
    """
    from orchestrator.router import route as route_prompt

    # Defensive trim: Query's ``min_length=1`` only catches the
    # empty-string case, not a prompt that's all whitespace. The
    # router itself lowercases + contains-checks, so a whitespace-
    # only prompt would still produce a (low-quality) routing
    # decision — rejecting it here surfaces the problem to the
    # caller instead.
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="prompt must not be empty or whitespace-only",
        )

    # v38 first cut: split + dedup the optional ``locales`` query
    # parameter. FastAPI doesn't accept a list[str] via Query
    # cleanly across all OpenAPI consumers, so we accept a
    # comma-separated string and split it here. Empty string
    # (the default) is treated as "not provided" so the zero-cost
    # semantics hold (no locales → empty list echoed back).
    # NOTE: the v38 handler does NOT call _validate_locales_field
    # because that helper is not yet ported to master; the v39+
    # follow-up will add it (mirroring the branch's v52 Red).
    if locales is None or not locales.strip():
        resolved_locales: list[str] = []
    else:
        seen: set[str] = set()
        resolved_locales = []
        for raw in locales.split(","):
            code = raw.strip()
            if not code or code in seen:
                continue
            seen.add(code)
            resolved_locales.append(code)

    phase, hint = route_prompt(cleaned_prompt)
    logger.info(
        "api.route_preview",
        game=hint["game"],
        phase=phase,
        confidence=hint["confidence"],
        matched_keyword=hint["matched_keyword"],
        generator_count=len(hint["generators"]),
        locale_count=len(resolved_locales),
    )
    return RoutePreviewResponse(
        prompt=cleaned_prompt,
        game=hint["game"],
        phase=phase,
        generators=list(hint["generators"]),
        confidence=hint["confidence"],
        matched_keyword=hint["matched_keyword"],
        locales=resolved_locales,
    )


@router.get("/feature_flags", response_model=FeatureFlagsResponse, dependencies=[Depends(verify_api_key)])
async def get_feature_flags() -> FeatureFlagsResponse:
    """List all registered feature flags and their current state.

    This endpoint is unauthenticated by design — it surfaces the
    same flag values that internal call sites read via
    ``orchestrator.feature_flags.is_enabled()``, so operators can
    verify the live runtime state of staged P5 rollouts.

    The response is a snapshot of the in-process
    ``orchestrator.feature_flags._DEFAULT_FLAGS`` registry (plus any
    active overrides in ``_overrides``) at the moment of the call.
    There is no DB, no Redis, and no environment override layered
    on top — the values match exactly what the orchestrator would
    use to gate a real call site one line later in the same process.
    The endpoint is therefore safe to poll freely from a dashboard,
    but its output will diverge from a second process's output if
    the two processes were started with different overrides (by
    design — this is the "what is THIS process doing" view).

    The ``flags`` list is sorted by name (matching
    ``orchestrator.feature_flags.known_flags()``) so snapshot tests
    and dashboards can rely on a stable order. The endpoint does not
    write to any log sink besides the standard ``api.*`` event
    channel, and emits exactly one ``api.feature_flags.listed`` info
    event per call.

    Adapted from the discord-ops-hardening branch's
    ``get_feature_flags`` (source bundle line 1211-1245). The branch
    handler imported ``_FLAGS`` directly and read ``_FLAGS[name]``;
    master's ``orchestrator.feature_flags`` module exposes the live
    value through ``is_enabled(name)`` (which resolves
    ``_overrides`` first, then falls back to ``_DEFAULT_FLAGS``).
    The response shape is byte-identical to the branch's so a client
    written against the branch contract still works against the
    master module.

    Note: registered after ``/mods/*`` introspection endpoints
    because ``/feature_flags`` does not collide with any ``{x}``
    path parameter — it is a static path under the ``/v1`` prefix.
    """
    from orchestrator.feature_flags import known_flags, is_enabled

    flags = [
        FeatureFlagValue(name=name, enabled=is_enabled(name))
        for name in known_flags()
    ]
    logger.info("api.feature_flags.listed", count=len(flags))
    return FeatureFlagsResponse(flags=flags, count=len(flags))


@router.get("/feature_flags/history", response_model=FlagHistoryResponse, dependencies=[Depends(verify_api_key)])
async def get_feature_flag_history(
    flag_name: Annotated[
        str | None,
        Query(
            description=(
                "Optional filter — only return audit-log rows whose "
                "``name`` matches this value exactly. Omit to "
                "return rows for every registered flag."
            ),
            max_length=128,
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            description=(
                "Maximum number of audit-log rows to return. "
                "Clamped to 1..1000 by FastAPI. The endpoint returns "
                "the FIRST N rows (most recent) because the audit "
                "log is already newest-first — slicing from the "
                "front preserves the natural order without a "
                "reverse."
            ),
            ge=1,
            le=1000,
        ),
    ] = 100,
) -> FlagHistoryResponse:
    """Return the in-memory feature-flag override audit log.

    Companion endpoint to ``GET /v1/feature_flags`` (read) and
    ``POST /v1/feature_flags/{name}`` (write): surfaces the
    audit trail of every override event recorded by
    :func:`orchestrator.feature_flags.record_override` /
    :func:`orchestrator.feature_flags.set_flag`, so operators
    can answer "who flipped which flag when" without scraping
    structlog output or grepping a log shipper.

    The endpoint is unauthenticated by design, mirroring the GET
    and POST siblings. All three endpoints are intended for an
    internal operator dashboard on a trusted network; production
    deployments should gate them behind an ingress-level ACL or
    a ``verify_api_key`` check before exposing them publicly.
    Adding auth here is a one-line change
    (``Depends(verify_api_key)``) and does not require touching
    the rest of the function.

    The audit log is process-local — it lives in the
    ``orchestrator.feature_flags._history`` module-level deque
    (capped at ``_HISTORY_LIMIT`` = 100 rows) and resets on every
    process restart. There is no Redis, DB, or environment
    override layered on top; the values match exactly what the
    orchestrator would have logged in this same process, one
    line later. The endpoint is therefore safe to poll freely
    from a dashboard, but its output will diverge from a second
    process's output if the two processes were started
    independently (by design — this is the "what is THIS
    process doing" view).

    Query parameters:

    - ``flag_name`` — optional exact-match filter against the
      :class:`orchestrator.feature_flags.FlagOverride.name`
      attribute. Omit to see every flag. An unknown flag name
      returns an empty list with ``total=0`` (it is NOT a 404 —
      the audit log is a query, not a registry lookup, and "no
      rows match" is a legitimate result).
    - ``limit`` — default 100, clamped to 1..1000 by FastAPI's
      ``Query`` validator; out-of-range values return 422.
      Returns the FIRST N rows (most recent) because the audit
      log is already newest-first (see
      :func:`orchestrator.feature_flags.get_history`).

    ``total`` is the count of rows that matched the filter
    BEFORE the ``limit`` clamp, so a caller can detect that the
    history has grown past the page size and request the next
    page (future ``before`` cursor — not implemented in v36).
    """
    from orchestrator.feature_flags import get_history

    history = get_history(name=flag_name)

    total = len(history)
    # ``get_history`` already returns newest-first, so slicing
    # from the front preserves the natural order without a
    # reverse. Slicing with a value larger than ``total`` returns
    # the full list (no IndexError).
    page = history[:limit]
    entries = [
        FlagHistoryEntry(
            name=event.name,
            value=event.value,
            reason=event.reason,
            actor=event.actor,
        )
        for event in page
    ]

    logger.info(
        "api.feature_flag.history_read",
        total=total,
        flag_name_filter=flag_name,
        returned=len(entries),
    )
    return FlagHistoryResponse(entries=entries, total=total)


@router.post(
    "/feature_flags/{name}",
    response_model=FeatureFlagChangeResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_feature_flag(
    name: str,
    body: FeatureFlagUpdate,
) -> FeatureFlagChangeResponse:
    """Toggle a single feature flag at runtime.

    Companion endpoint to ``GET /v1/feature_flags``: surfaces the
    live runtime state on read, and lets an operator flip that
    state on write. The flag name comes from the URL path
    (``{name}``) — the same name the GET endpoint surfaces — and
    the desired new value (``enabled``) comes from the JSON body.

    The endpoint is unauthenticated by design, mirroring the GET
    sibling. Both endpoints are intended for an internal operator
    dashboard on a trusted network; production deployments should
    gate them behind an ingress-level ACL or move the toggle behind
    a ``verify_api_key`` check before exposing them publicly.
    Adding auth here is a one-line change (``Depends(verify_api_key)``)
    and does not require touching the rest of the function.

    On an unknown flag name, the endpoint returns 404 with a
    clear detail message. The body validation for ``enabled`` is
    handled by FastAPI's automatic Pydantic validation: a missing
    or wrong-typed ``enabled`` field returns 422 (the FastAPI
    default for request-body validation errors), and a missing
    body returns 422 as well — both are surfaced to the caller as
    JSON ``detail`` arrays and are not caught by this handler.

    If the flag is currently locked (via ``pin_flag``) AND the
    requested value differs from the pinned value, the underlying
    ``set_flag`` raises :class:`FlagPinnedError`, which this
    handler maps to a 423 Locked response with a clear detail
    message identifying the pinned flag. No-op writes to a pinned
    flag (the operator re-submits the value the flag already
    holds) succeed silently — the pin guard is a "no drift"
    guard, not a "no read" guard. The 423 mapping is a v39
    addition over the branch source; the branch does not handle
    FlagPinnedError because the branch's cleanroom module has no
    ``pin_flag`` lock semantics (its ``record_flag_change``
    helper never raises on a pinned flag).

    Every successful change emits an
    ``api.feature_flag.updated`` info log event with the flag
    name, the previous value, and the new value. A no-op write
    (the flag was already in the requested state) still emits the
    log event so the audit trail captures every operator call,
    not just the ones that mutated state. The audit append flows
    through ``orchestrator.feature_flags.record_override`` (with
    ``reason="set_flag"`` and ``actor="system"``) and surfaces in
    ``GET /v1/feature_flags/history`` alongside operator-pinned
    overrides.

    Adapted from the discord-ops-hardening branch's
    ``update_feature_flag`` (source bundle line 1248-1315). The
    branch's handler reads ``orchestrator.feature_flags._FLAGS[name]``
    directly; master delegates to ``set_flag(name, enabled)`` so
    the audit append uses the master's ``FlagOverride`` dataclass
    rather than the branch's dict-shaped audit type. The wire
    shape is byte-identical to the branch's contract.
    """
    from orchestrator.feature_flags import FlagPinnedError, set_flag

    try:
        previous_value = set_flag(name=name, enabled=body.enabled)
    except FlagPinnedError as exc:
        # The pin guard fires when a locked flag is asked to
        # drift to a different value. Map to 423 Locked (RFC
        # 4918) so an operator dashboard can render "this flag
        # is pinned — unpin it first" without parsing 500-class
        # responses. The current value is included in the
        # detail so the caller can confirm what the pin guard
        # is actually defending against.
        logger.warning(
            "api.feature_flag.update_locked",
            flag_name=exc.flag_name,
            current_value=exc.current_value,
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"feature flag {exc.flag_name!r} is pinned to "
                f"{exc.current_value}; unpin_flag() before mutating"
            ),
        )

    if previous_value is None:
        # Unknown flag — set_flag logged feature_flag.unknown at
        # warning level; mirror that here at the api.* channel so
        # operator dashboards that filter on api.* see the 404
        # correlation directly.
        logger.warning(
            "api.feature_flag.update_unknown",
            flag_name=name,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature flag: {name!r}",
        )
    logger.info(
        "api.feature_flag.updated",
        flag_name=name,
        previous_value=previous_value,
        new_value=body.enabled,
    )
    return FeatureFlagChangeResponse(
        name=name,
        enabled=body.enabled,
        previous_value=previous_value,
    )


@router.post(
    "/feature_flags/{name}/rollback",
    response_model=FeatureFlagRollbackResponse,
    dependencies=[Depends(verify_api_key)],
)
async def rollback_feature_flag(name: str) -> FeatureFlagRollbackResponse:
    """Roll back the most recent real change to a single feature flag.

    Companion endpoint to ``GET /v1/feature_flags`` (read),
    ``POST /v1/feature_flags/{name}`` (toggle), and
    ``GET /v1/feature_flags/history`` (audit log): closes the
    operator-dashboard loop on a single flag with a single-call
    undo for the most recent mutation. Together the four
    endpoints let an operator read the live registry, flip a
    flag, audit who changed what when, and undo a mistaken
    toggle without restarting the process or hand-editing the
    audit log.

    The flag name comes from the URL path (``{name}``); there
    is intentionally no request body because rollback has no
    parameters — the most recent non-no-op entry in the audit
    log is the source of truth for what to restore, and
    accepting a body field for "what to roll back to" would
    duplicate (and could contradict) the audit log itself.

    The endpoint is unauthenticated by design, mirroring the
    toggle endpoint (v39) and the read-side siblings. All four
    endpoints are intended for an internal operator dashboard on
    a trusted network; production deployments should gate them
    behind an ingress-level ACL or a ``verify_api_key`` check
    before exposing them publicly. Adding auth here is a
    one-line change (``Depends(verify_api_key)``) and does not
    require touching the rest of the function.

    Status code mapping mirrors the toggle endpoint plus two
    new branches:

    - ``200 OK`` — rollback succeeded; the response body is a
      :class:`FeatureFlagRollbackResponse` describing what was
      undone.
    - ``404 Not Found`` — ``name`` is not a registered feature
      flag. Mirrors the toggle contract: a typo in the path
      fails closed and surfaces ``{"detail": "Unknown feature
      flag: '<name>'"}`` so an operator dashboard can render
      the exact flag name that was rejected.
    - ``409 Conflict`` — the flag IS registered, but the audit
      log has no non-no-op entry for it (either the history is
      empty for this flag, or every recorded entry for this
      flag is a no-op write — e.g. the operator toggled the
      flag back to its previous value before the rollback
      request landed). The 409 is intentional: the request was
      well-formed and the flag exists, so 404 would be a lie;
      422 (validation) would also be wrong because the request
      has no body to validate. 409 is the standard "the resource
      state prevents the operation" code.

    Every successful rollback emits an
    ``api.feature_flag.rolled_back`` info log event with the
    flag name, the value the flag held before the rollback,
    the value it was rolled back to, the index of the restored
    audit entry, and the size of the audit log at rollback
    time. The underlying ``set_flag`` call inside the helper
    emits its own ``feature_flag.override_recorded`` event for
    the actual write, so a log search for
    ``feature_flag.override_recorded flag_name=<name>``
    surfaces the rollback in the same channel as every other
    mutation.

    The rollback itself is recorded as a normal
    ``feature_flag.override_recorded`` entry, so a subsequent
    call to ``GET /v1/feature_flags/history`` immediately
    reflects the rollback in chronological order without any
    enrichment.

    Adapted from the discord-ops-hardening branch's
    ``rollback_feature_flag`` (source bundle line 1416-1529).
    The two key adaptations:

    1. The branch handler imports ``_FLAGS`` from
       ``orchestrator.feature_flags`` (the branch's cleanroom
       module used a single ``_FLAGS`` dict). Master split the
       branch's dict into ``_DEFAULT_FLAGS`` (read-only
       defaults) and ``_overrides`` (the mutable live state)
       as part of the v33-v39 audit-log rewrite. The
       "is this flag known?" check is therefore
       ``name in _DEFAULT_FLAGS or name in _overrides`` rather
       than ``name in _FLAGS``.

    2. The branch's helper signature returns the audit entry's
       ``no_op`` flag explicitly. Master's ``rollback_flag``
       returns ``None`` for both "unknown flag" and "no
       rollbackable history" (these are the only two cases
       that should ever trigger a non-200 response). The 404 vs
       409 distinction is recovered at the route layer by
       re-checking the registry, exactly as the source does.

    The wire shape is byte-identical to the branch's contract
    so a client written against the branch's response can
    switch to master without any code change.
    """
    from orchestrator.feature_flags import (
        _DEFAULT_FLAGS,
        _overrides,
        rollback_flag,
    )

    result = rollback_flag(name)
    if result is None:
        # Distinguish 404 (unknown flag) from 409 (no
        # rollbackable history) by re-checking the registry. The
        # helper already logs ``feature_flag.unknown`` at
        # warning level for the unknown case; we mirror that
        # here at the ``api.*`` channel so operator dashboards
        # filtering on ``api.*`` see the correlation directly.
        is_known = name in _DEFAULT_FLAGS or name in _overrides
        if not is_known:
            logger.warning(
                "api.feature_flag.rollback_unknown",
                flag_name=name,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown feature flag: {name!r}",
            )
        # Known flag but no rollbackable history → 409.
        logger.info(
            "api.feature_flag.rollback_no_history",
            flag_name=name,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No rollback history available for feature flag: {name!r}"
            ),
        )

    logger.info(
        "api.feature_flag.rolled_back",
        flag_name=result["name"],
        rolled_back_from=result["rolled_back_from"],
        rolled_back_to=result["rolled_back_to"],
        restored_entry_index=result["restored_entry_index"],
        history_size_at_rollback=result["history_size_at_rollback"],
    )
    # Field-by-field copy: ``rollback_flag`` returns
    # ``dict[str, object]`` (master's intentional widen over the
    # branch's ``dict[str, Any]`` so a future ``object``-typed
    # audit entry cannot silently bypass the schema), and Pyright
    # rejects ``**result`` unpacking into typed Pydantic fields.
    # The wire shape is byte-identical to ``**result`` so clients
    # see no difference.
    return FeatureFlagRollbackResponse(
        name=result["name"],  # type: ignore[arg-type]
        rolled_back_from=result["rolled_back_from"],  # type: ignore[arg-type]
        rolled_back_to=result["rolled_back_to"],  # type: ignore[arg-type]
        restored_entry_index=result["restored_entry_index"],  # type: ignore[arg-type]
        history_size_at_rollback=result["history_size_at_rollback"],  # type: ignore[arg-type]
    )


@router.post(
    "/feature_flags/{name}/pin",
    response_model=FeatureFlagPinResponse,
    dependencies=[Depends(verify_api_key)],
)
async def pin_feature_flag(name: str) -> FeatureFlagPinResponse:
    """Pin a single feature flag so future mutations are rejected.

    The natural complement to v40's
    ``POST /v1/feature_flags/{name}/rollback`` endpoint (undo)
    and the v39/v15/v16/v17 quartet: lets an operator mark a
    flag as locked without deleting it from the registry. Useful
    for staging rollouts where the operator wants to flip a flag
    on for the rollout and then guarantee it cannot be accidentally
    toggled back off by a flaky dashboard.

    The flag name comes from the URL path (``{name}``); there is
    intentionally no request body because pin has no parameters —
    the operator either pins or doesn't, and the response surfaces
    the result.

    The endpoint is unauthenticated by design, mirroring the
    v15/v16/v17/v18/v40 siblings. All six endpoints are intended
    for an internal operator dashboard on a trusted network;
    production deployments should gate them behind an
    ingress-level ACL or a ``verify_api_key`` check before
    exposing them publicly. Adding auth here is a one-line
    change (``Depends(verify_api_key)``) and does not require
    touching the rest of the function.

    Status code mapping:

    - ``200 OK`` — pin succeeded. The response body is a
      :class:`FeatureFlagPinResponse` describing the new pin
      state, whether the call was a no-op (``already_pinned``),
      and the flag's current value. Pinning an already-pinned flag
      is NOT an error — it returns 200 with ``already_pinned=True``
      so dashboards can render a single "lock applied" view without
      branching on the response shape.
    - ``404 Not Found`` — ``name`` is not a registered feature
      flag. Mirrors v16/v18/v40's contract: a typo in the path
      fails closed and surfaces ``{"detail": "Unknown feature
      flag: '<name>'"}`` so an operator dashboard can render the
      exact flag name that was rejected.

    Every successful pin (including no-op re-pins) emits an
    ``api.feature_flag.pinned`` info log event with the flag name
    and the current value. The helper ``pin_flag()`` emits its own
    ``feature_flag.pinned_by_operator`` info log on a fresh pin
    (the no-op case stays silent there to avoid double-logging),
    so a log search for either event surfaces the operator
    activity in the same channel as every other flag mutation.

    Pin state is process-local — it lives in the
    ``orchestrator.feature_flags._locked_pins`` module-level set
    and resets on every process restart. There is no Redis, DB,
    or environment override layered on top. Operators who need
    a persistent lock should unpin-and-pin at startup or extend
    this module to read an env var override on import
    (intentionally not done today — pinning is a runtime,
    in-the-moment decision, not a deployment-time decision).

    Once a flag is pinned, :func:`set_flag` (and therefore
    :func:`rollback_flag`, which routes through ``set_flag``) will
    raise :class:`FlagPinnedError`. The exception is NOT caught
    here — it propagates to FastAPI's default 500 handler. That is
    intentional: a pinned flag's mutation rejection is a
    programming error or a deliberate test path, not a normal
    200 response. A future hardening could catch the exception
    in this route and surface it as a 423 Locked response, but
    adding that catch here would create a tight coupling between
    the toggle endpoint and the pin endpoint that does not
    currently exist.

    Adapted from the discord-ops-hardening branch's
    ``pin_feature_flag`` handler (source bundle line 1532-1626).
    The branch's handler reads ``orchestrator.feature_flags._FLAGS[name]``
    directly; master delegates to ``pin_flag(name)`` and uses
    ``name in _DEFAULT_FLAGS or name in _overrides`` for the
    unknown-flag check (the branch used ``name in _FLAGS``). The
    wire shape is byte-identical to the branch's contract.
    """
    from orchestrator.feature_flags import pin_flag

    result = pin_flag(name)
    if result is None:
        # Mirrors v16/v18/v40: unknown flag -> 404. The helper
        # already logs ``feature_flag.unknown`` at warning
        # level; mirror that here at the ``api.*`` channel so
        # operator dashboards filtering on ``api.*`` see the
        # correlation directly.
        logger.warning(
            "api.feature_flag.pin_unknown",
            flag_name=name,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature flag: {name!r}",
        )

    logger.info(
        "api.feature_flag.pinned",
        flag_name=result["name"],
        current_value=result["current_value"],
        already_pinned=result["already_pinned"],
    )
    # Field-by-field copy: ``pin_flag`` returns
    # ``dict[str, object]`` (master's intentional widen over the
    # branch's ``dict[str, Any]``), and Pyright rejects ``**result``
    # unpacking into typed Pydantic fields. ``was_pinned`` is
    # not in ``pin_flag``'s return (only ``unpin_flag`` sets it),
    # so we hard-code it to False on the pin endpoint — the
    # wire shape is byte-identical to ``**result`` plus the
    # False sentinel.
    return FeatureFlagPinResponse(
        name=result["name"],  # type: ignore[arg-type]
        pinned=result["pinned"],  # type: ignore[arg-type]
        already_pinned=result["already_pinned"],  # type: ignore[arg-type]
        was_pinned=False,
        current_value=result["current_value"],  # type: ignore[arg-type]
    )


@router.post(
    "/feature_flags/{name}/unpin",
    response_model=FeatureFlagPinResponse,
    dependencies=[Depends(verify_api_key)],
)
async def unpin_feature_flag(name: str) -> FeatureFlagPinResponse:
    """Remove the pin on a single feature flag so mutations succeed again.

    Companion endpoint to v41's
    ``POST /v1/feature_flags/{name}/pin`` (undo) and the
    v39/v15/v16/v17 quartet: lets an operator unlock a flag
    without rolling back its current value. Useful when a
    staging rollout is over and the operator wants the
    dashboard toggle to work again, or when a debugging
    session needs to flip the flag off without first deleting
    the lock.

    The flag name comes from the URL path (``{name}``); there
    is intentionally no request body because unpin has no
    parameters — the operator either removes the lock or
    doesn't, and the response surfaces the result.

    The endpoint is unauthenticated by design, mirroring the
    v15/v16/v17/v18/v40/v41 pin/rollback/toggle/history/
    registry siblings. All seven endpoints are intended for
    an internal operator dashboard on a trusted network;
    production deployments should gate them behind an
    ingress-level ACL or a ``verify_api_key`` check before
    exposing them publicly. Adding auth here is a one-line
    change (``Depends(verify_api_key)``) and does not
    require touching the rest of the function.

    Status code mapping:

    - ``200 OK`` — unpin succeeded. The response body is a
      :class:`FeatureFlagPinResponse` with ``pinned=False``
      and ``was_pinned`` indicating whether the call
      actually removed a lock (``True``) or was a no-op
      (``False``). Unpinning an unpinned flag is NOT an
      error — it returns 200 with ``was_pinned=False`` so
      dashboards can render a single "lock removed" view
      without branching on the response shape.
    - ``404 Not Found`` — ``name`` is not a registered feature
      flag. Mirrors v16/v18/v40/v41's contract: a typo in
      the path fails closed and surfaces
      ``{"detail": "Unknown feature flag: '<name>'"}``.

    Every successful unpin (including no-op un-unpins) emits
    an ``api.feature_flag.unpinned`` info log event with the
    flag name and the current value. The helper
    ``unpin_flag()`` emits its own
    ``feature_flag.unpinned_by_operator`` info log on a real
    unpin (the no-op case stays silent there to avoid
    double-logging), so a log search for either event
    surfaces the operator activity in the same channel as
    every other flag mutation.

    Pin state is process-local — it lives in the
    ``orchestrator.feature_flags._locked_pins`` module-level
    set and resets on every process restart. There is no
    Redis, DB, or environment override layered on top.

    Once a flag is unpinned, :func:`set_flag` (and therefore
    :func:`rollback_flag`, which routes through ``set_flag``)
    will succeed again. The :class:`FlagPinnedError` path
    remains intact in the toggle/rollback endpoints — this
    endpoint just clears the lock state, it does not
    catch the exception itself (mirroring v41's pin handler:
    a pinned flag's mutation rejection is a programming
    error or a deliberate test path, not a normal 200
    response).

    Adapted from the discord-ops-hardening branch's
    ``unpin_feature_flag`` handler (source bundle line
    1629-1700). The branch's handler reads
    ``orchestrator.feature_flags._FLAGS[name]`` directly;
    master delegates to ``unpin_flag(name)`` and uses
    ``name in _DEFAULT_FLAGS or name in _overrides`` for the
    unknown-flag check (the branch used ``name in _FLAGS``).
    The wire shape is byte-identical to the branch's
    contract.
    """
    from orchestrator.feature_flags import unpin_flag

    result = unpin_flag(name)
    if result is None:
        # Mirrors v16/v18/v40/v41: unknown flag -> 404. The
        # helper already logs ``feature_flag.unknown`` at
        # warning level; mirror that here at the ``api.*``
        # channel so operator dashboards filtering on
        # ``api.*`` see the correlation directly.
        logger.warning(
            "api.feature_flag.unpin_unknown",
            flag_name=name,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature flag: {name!r}",
        )

    logger.info(
        "api.feature_flag.unpinned",
        flag_name=result["name"],
        current_value=result["current_value"],
        was_pinned=result["was_pinned"],
    )
    # Field-by-field copy: ``unpin_flag`` returns
    # ``dict[str, object]`` (master's intentional widen over
    # the branch's ``dict[str, Any]``), and Pyright rejects
    # ``**result`` unpacking into typed Pydantic fields.
    # ``already_pinned`` is NOT in ``unpin_flag``'s return
    # (only ``pin_flag`` sets it), so we hard-code it to
    # False on the unpin endpoint — the field exists in the
    # shared schema but the wire shape from the unpin
    # path never observes a pre-existing pin via the
    # ``already_pinned`` sentinel (that role is filled by
    # ``was_pinned`` on this side).
    return FeatureFlagPinResponse(
        name=result["name"],  # type: ignore[arg-type]
        pinned=result["pinned"],  # type: ignore[arg-type]
        already_pinned=False,
        was_pinned=result["was_pinned"],  # type: ignore[arg-type]
        current_value=result["current_value"],  # type: ignore[arg-type]
    )


@router.get(
    "/feature_flags/{name}/pin",
    response_model=FeatureFlagPinStateResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_feature_flag_pin_state(name: str) -> FeatureFlagPinStateResponse:
    """Read the current pin state of a single feature flag.

    Companion endpoint to v41's ``POST /v1/feature_flags/{name}/pin``
    and v42's ``POST /v1/feature_flags/{name}/unpin``. The POST
    endpoints have a side effect (they toggle the flag's membership
    in the in-memory ``_locked_pins`` set); this GET is the
    read-only counterpart — it surfaces the live pin state without
    mutating any registry, so an operator dashboard can poll "is
    this flag locked?" as often as it likes without worrying about
    duplicate toggles or audit-log pollution.

    Together with v41 and v42, this GET completes the pin-state
    surface for the operator dashboard:

    - ``POST /pin`` — pin the flag (idempotent: 200 even on re-pin).
    - ``POST /unpin`` — unpin the flag (idempotent: 200 even on
      un-unpin).
    - ``GET /pin`` — *this endpoint.* Read-only snapshot of the
      current pin state and value, no mutation.

    The flag name comes from the URL path (``{name}``); there is
    intentionally no request body because the GET has no
    parameters and no query-string filter — the endpoint is the
    simplest possible read on a single flag.

    The endpoint is unauthenticated by design, mirroring the
    v15/v16/v17/v18/v41/v42 pin/rollback/toggle/history/registry
    siblings. Pin state is process-local and resets on every
    process restart (same lifecycle as ``_DEFAULT_FLAGS``,
    ``_overrides``, and ``_locked_pins``).

    Status code mapping:

    - ``200 OK`` — the flag is registered. The response body is a
      :class:`FeatureFlagPinStateResponse` with ``name`` echoed
      back, ``pinned`` mirroring ``is_pinned(name)``,
      ``current_value`` mirroring ``is_enabled(name)``, and
      ``known=True``.
    - ``404 Not Found`` — ``name`` is not a registered feature
      flag. Mirrors the v41/v42 contract: a typo in the path fails
      closed and surfaces ``{"detail": "Unknown feature flag:
      '<name>'"}``.

    Every successful 200 emits an ``api.feature_flag.pin_state``
    info log event with ``flag_name``, ``pinned``,
    ``current_value``, and ``known=True``. Every 404 emits an
    ``api.feature_flag.pin_state_unknown`` warning log event with
    ``flag_name`` and ``known_flags=sorted(_DEFAULT_FLAGS.keys())``,
    mirroring the helper layer's ``feature_flag.unknown`` payload
    so dashboards can spot typos in operator URL paths.

    Adapted from the discord-ops-hardening branch's
    ``get_feature_flag_pin_state`` handler (source bundle line
    1692-1774). Adaptations vs. the source:

    - The branch's handler reads ``orchestrator.feature_flags._FLAGS``
      and ``orchestrator.feature_flags._PINNED_FLAGS`` directly;
      master delegates to ``is_pinned(name)`` and ``is_enabled(name)``
      helpers (which read ``_locked_pins`` and the
      ``_DEFAULT_FLAGS`` ∪ ``_overrides`` union respectively), and
      the "is the flag known?" check uses ``name in _DEFAULT_FLAGS
      or name in _overrides`` rather than the source's
      ``known_flags()`` membership test (which only returns
      defaults — overriding a non-default name would 404 here on
      master but 200 on the branch; the wider check matches the
      v41/v42 pin/unpin handlers and the ``is_enabled(name)``
      contract).
    - The branch uses ``dict[str, Any]`` for the helper return
      type; master uses ``dict[str, object]`` (Pyright's preferred
      widening). No field-by-field copy is needed here because
      the handler reads scalar booleans from the helpers rather
      than a single return dict.
    - The 200 response hard-codes ``known=True`` because the
      404 ``HTTPException`` path is the only way to surface an
      unknown flag — mirrors the source's hard-coded
      ``known=True`` on the 200 branch.

    The wire shape is byte-identical to the branch's contract.
    """
    from orchestrator.feature_flags import (
        is_enabled,
        is_pinned,
        _DEFAULT_FLAGS,
        _overrides,
    )

    if name not in _DEFAULT_FLAGS and name not in _overrides:
        logger.warning(
            "api.feature_flag.pin_state_unknown",
            flag_name=name,
            known_flags=sorted(_DEFAULT_FLAGS.keys()),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature flag: {name!r}",
        )

    pinned = is_pinned(name)
    current_value = is_enabled(name)
    logger.info(
        "api.feature_flag.pin_state",
        flag_name=name,
        pinned=pinned,
        current_value=current_value,
        known=True,
    )
    return FeatureFlagPinStateResponse(
        name=name,
        pinned=pinned,
        current_value=current_value,
        known=True,
    )


@router.get(
    "/feature_flags/pins",
    response_model=FeatureFlagPinsResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_feature_flag_pins() -> FeatureFlagPinsResponse:
    """List every currently-pinned feature flag (collection view).

    The v44 collection-level companion to v43's
    ``GET /v1/feature_flags/{name}/pin``: where the v43 endpoint
    surfaces the pin state of a *single* flag, this endpoint
    surfaces every pinned flag in a single flat response — so an
    operator dashboard can render the entire locked-set with one
    HTTP round-trip rather than looping over
    ``GET /v1/feature_flags/{name}/pin`` for every registered flag.

    Together with the v41 POST ``/pin`` / ``/unpin`` endpoints and
    v43's GET ``/{name}/pin``, the four endpoints complete the
    pin-state surface:

    - ``POST /v1/feature_flags/{name}/pin`` — pin a flag
      (idempotent).
    - ``POST /v1/feature_flags/{name}/unpin`` — unpin a flag
      (idempotent).
    - ``GET /v1/feature_flags/{name}/pin`` — single-flag pin state
      snapshot.
    - ``GET /v1/feature_flags/pins`` — *this endpoint.* Collection
      view of every currently-pinned flag, sorted by name.

    The endpoint is unauthenticated by design, matching the
    v15/v16/v17/v18/v41/v42/v43 pin/rollback/toggle/history/
    registry siblings. Pin state is process-local and resets on
    every process restart (same lifecycle as ``_DEFAULT_FLAGS`` /
    ``_overrides`` / ``_locked_pins`` / ``_history``).

    Status code mapping:

    - ``200 OK`` — always 200. The collection is allowed to be
      empty (no flags are currently pinned); the response shape
      is ``{"pins": [], "count": 0}`` rather than a 404 so
      dashboards can render an empty "no flags pinned" state
      without special-casing the error path. Mirrors the v15
      ``GET /v1/feature_flags`` empty-set contract.

    Every successful 200 emits an ``api.feature_flag.pins_listed``
    info log event with ``count`` (length of the ``pins`` list)
    and ``pinned_count`` (alias for ``count`` — included as a
    separate field so dashboards grepping for ``pinned_count=``
    can find both this endpoint and the v41/v42 POST ``/pin`` /
    ``/unpin`` events on the same key).

    Adapted from the discord-ops-hardening branch's
    ``get_feature_flag_pins`` handler (source bundle line
    1777-1842). Adaptations vs. the source:

    - The branch's handler calls ``get_pinned_flags()`` and
      ``is_enabled(name)`` directly from the helper module;
      master routes the same way (those names exist on master's
      ``orchestrator.feature_flags`` per the round 1 / round 2 /
      round 21 ports — ``get_pinned_flags`` returns
      ``tuple(sorted(_locked_pins))`` and ``is_enabled`` resolves
      ``_overrides[name]`` or ``_DEFAULT_FLAGS[name]``). The wire
      shape is byte-identical to the branch's contract.
    - The branch uses ``dict[str, Any]`` for any helper return
      type; master's helpers return ``dict[str, object]`` (a
      Pyright-preferred widening) for the pin/unpin helpers, but
      this handler reads scalar booleans from the helpers (no
      dict round-trip) so the typing difference does not affect
      the port.
    - The list-comprehension that builds the ``pins`` entries
      delegates the per-flag value lookup to ``is_enabled(name)``
      rather than reading ``_FLAGS[name]`` directly. The result
      is the same for the simple on/off case; the helper call
      is the consistent seam for any future resolution rule
      changes (e.g. percentage-rollout support that lands in a
      later PR). The route is intentionally a thin wrapper
      around the helper pair, not a parallel implementation.

    The wire shape (``{"pins": [{"name", "current_value"}, ...],
    "count": int}``) is byte-identical to the branch's contract.
    """
    from orchestrator.feature_flags import get_pinned_flags, is_enabled

    pinned_names = get_pinned_flags()
    pins = [
        FeatureFlagPinSummary(
            name=name,
            current_value=is_enabled(name),
        )
        for name in pinned_names
    ]
    logger.info(
        "api.feature_flag.pins_listed",
        count=len(pins),
        pinned_count=len(pins),
    )
    return FeatureFlagPinsResponse(
        pins=pins,
        count=len(pins),
    )


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


# NOTE: ``/mods/stats`` is registered BEFORE ``/mods/{request_id}``
# because FastAPI's path matching is declaration-order sensitive —
# a request to ``/v1/mods/stats`` would otherwise be captured by the
# generic ``{request_id}`` route with ``request_id="stats"``. The
# same defensive ordering is used for ``/mods/cancellation_reasons``
# and ``/mods/generators`` elsewhere in this file.
@router.get("/mods/stats", response_model=StatsResponse)
async def get_mod_stats(request: Request) -> Response:
    """Return aggregate mod-request stats for an operator dashboard.

    Pure read-only: returns the total row count plus breakdowns by
    ``status`` and ``phase`` for the ``mod_requests`` table. No
    filters, no pagination, no auth — this is a global operator
    view. If you need per-user or per-tenant stats, add a
    parameterized variant; do not overload this one.

    Both breakdowns are computed by a single helper
    (:func:`storage.queries.get_mod_request_stats`) that issues one
    ``COUNT(*)`` and two ``GROUP BY`` queries inside a single
    session. The total counts of ``by_status[*].count`` and
    ``by_phase[*].count`` should each equal ``total`` (modulo
    requests with ``phase IS NULL``, which are surfaced under the
    synthetic key ``__none__`` in the phase breakdown).

    The endpoint is intentionally cheap and has no side effects —
    it can be polled freely from a dashboard without coordinating
    with any other state. The ``generated_at`` field tells the
    caller exactly when the numbers were computed so they can
    detect staleness without trusting their own clock.

    v77 F2 (ETag on /v1/mods/stats): the response carries
    a strong ``ETag: "<sha256>"`` header (sha256 of the
    *stable projection* — ``total`` + ``by_status`` +
    ``by_phase`` — NOT ``generated_at``). Matching
    ``If-None-Match`` short-circuits to 304 (no body, just
    the ETag). ETag value is wrapped in double quotes per
    RFC 7232.
    """
    raw = await get_mod_request_stats()

    # The helper returns plain dicts so it can be tested without
    # importing pydantic. The route is the boundary that pins the
    # public contract — every dict is mapped through a Pydantic
    # model here so clients can rely on field-level validation.
    by_status: list[StatusBreakdown] = [
        StatusBreakdown(status=row["status"], count=int(row["count"]))
        for row in raw.get("by_status", [])
    ]
    by_phase: list[PhaseBreakdown] = [
        PhaseBreakdown(phase=row["phase"], count=int(row["count"]))
        for row in raw.get("by_phase", [])
    ]

    logger.info(
        "api.mods.stats_returned",
        total=int(raw.get("total", 0)),
        by_status_count=len(by_status),
        by_phase_count=len(by_phase),
    )

    # v77 F2: hash the STABLE projection (data fields only,
    # excluding ``generated_at``). ``sort_keys=True`` pins
    # the key order for determinism.
    stable_projection = {
        "total": int(raw.get("total", 0)),
        "by_status": [{"status": b.status, "count": b.count} for b in by_status],
        "by_phase": [{"phase": b.phase, "count": b.count} for b in by_phase],
    }
    body_bytes = json.dumps(stable_projection, sort_keys=True).encode("utf-8")
    etag = hashlib.sha256(body_bytes).hexdigest()
    # Accept If-None-Match with OR without wrapping quotes
    # (some proxies strip them).
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None and if_none_match.strip() in (etag, f'"{etag}"'):
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    return JSONResponse(
        content=StatsResponse(
            total=int(raw.get("total", 0)),
            by_status=by_status,
            by_phase=by_phase,
            generated_at=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
        headers={"ETag": f'"{etag}"'},
    )


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


@router.get("/mods/{request_id}/metadata", response_model=ModMetadataResponse)
async def get_mod_metadata(request_id: str) -> ModMetadataResponse:
    """Get packaged metadata + version info for a completed request.

    Reads ``metadata.json`` and ``version.json`` from the packaged zip
    on disk via ``generators.packager.read_zip``. Returns empty dicts
    if the files are missing (older zips without ``version.json``, or
    a request that exists but isn't packaged yet) — the endpoint is
    idempotent and never 404s for a request that has a row in
    ``mod_outputs`` but no zip yet.

    Note: this endpoint reads from the *packaged* zip, not from the
    live Redis pipeline state. A request that's still running will
    return ``metadata={} version={}`` because there's no zip on disk
    yet. If you need in-flight metadata (e.g. T2 panel verdicts
    during a run), use ``GET /v1/mods/{id}`` instead.
    """
    import json
    from generators.packager import read_zip

    output = await get_mod_output(request_id)
    if not output:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )
    zip_key = output.get("zip_key")
    if not zip_key:
        # Request exists but isn't packaged yet — return empty metadata.
        logger.info(
            "api.metadata.not_packaged",
            request_id=request_id,
        )
        return ModMetadataResponse(request_id=request_id, metadata={}, version={})

    try:
        zip_files = read_zip(zip_key)
    except (ValueError, OSError) as exc:
        # ValueError catches ``read_zip``'s zip_key validation
        # failures; OSError catches filesystem failures (missing zip,
        # permission denied). Both are non-fatal for the API contract
        # — log and surface a 500 so the client can retry.
        logger.warning(
            "api.metadata.read_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read packaged zip: {exc}",
        )

    # ``dict[str, object]`` (vs. bare ``dict``) is a type-checker
    # tightening that captures the upstream contract at the
    # construction site: ``ModMetadataResponse.metadata`` / ``.version``
    # are both ``dict[str, Any]`` (per the schema), and the values
    # here come from ``json.loads`` so the JSON parser decides the
    # actual inner types — ``object`` is the most permissive value
    # side that matches ``Any``. Local-only change, no runtime
    # effect, but mypy catches contract drift if either field ever
    # changes type.
    metadata: dict[str, object] = {}
    version: dict[str, object] = {}
    if "metadata.json" in zip_files:
        try:
            metadata = json.loads(zip_files["metadata.json"])
        except json.JSONDecodeError:
            logger.warning(
                "api.metadata.invalid_json",
                request_id=request_id,
                file="metadata.json",
            )
    if "version.json" in zip_files:
        try:
            version = json.loads(zip_files["version.json"])
        except json.JSONDecodeError:
            logger.warning(
                "api.metadata.invalid_json",
                request_id=request_id,
                file="version.json",
            )

    return ModMetadataResponse(
        request_id=request_id,
        metadata=metadata,
        version=version,
    )


async def _get_cancellation_reason_safe(request_id: str) -> str | None:
    """Read the cancellation reason from Redis; swallow transient errors.

    The summary endpoint calls this from two places (live Redis path
    and DB-fallback path). A transient Redis outage on the
    cancellation-reason read is non-fatal — the summary is still
    useful without the reason. A programming bug (e.g. an
    ``AttributeError``) would still propagate, which is the right
    behavior: it surfaces in tests instead of being masked as a
    transient outage.
    """
    from storage.redis import get_cancellation_reason
    try:
        return await get_cancellation_reason(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.summary.cancellation_reason_unavailable",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


@router.get("/mods/{request_id}/summary", response_model=ModSummaryResponse)
async def get_mod_summary(request_id: str) -> ModSummaryResponse:
    """Get a human-readable text summary of a mod request.

    Combines the cached Redis pipeline state (status, generators,
    T1/T2 outcomes) with the packaged zip's manifest (feature name,
    file count) into a single short text block. Cache-first: prefers
    Redis for live status, falls back to DB and then to the packaged
    zip when Redis is cold.
    """
    import json
    from storage.redis import get_pipeline_state
    from generators.packager import read_zip

    feature_name: str | None = None
    mod_id: str | None = None
    file_count: int = 0
    status: str = "unknown"
    created_at: str | None = None
    generators: list[str] = []
    t1_errors: list[str] = []
    t2_status: str = "unknown"
    t2_score: int | None = None
    t2_max_score: int | None = None
    t2_passed: bool | None = None
    cancellation_reason: str | None = None

    # Redis first for live in-flight state. Catch only expected
    # transient errors — a programming bug should still propagate
    # so it doesn't get masked as a transient outage.
    try:
        redis_state = await get_pipeline_state(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.summary.redis_error",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        redis_state = None

    if redis_state:
        status = redis_state.get("status", status)
        outputs = redis_state.get("outputs", {}) or {}
        for gen_name, gen_output in outputs.items():
            files = (gen_output or {}).get("files", {}) or {}
            file_count += len(files)
            if gen_name not in generators:
                generators.append(gen_name)
        t1_errors = list(redis_state.get("errors", []) or [])
        t2_score = redis_state.get("t2_score")
        t2_max_score = redis_state.get("t2_max_score")
        t2_passed = redis_state.get("t2_passed")
        t2_status = "passed" if t2_passed else ("failed" if t2_passed is False else "unknown")
        created_at = redis_state.get("created_at")
        if status == "cancelled":
            cancellation_reason = await _get_cancellation_reason_safe(request_id)
        # Pull the mod name from in-memory outputs if any generator
        # already produced a manifest.
        manifest_gen = outputs.get("manifest_generator") or {}
        manifest_files = (manifest_gen or {}).get("files", {}) or {}
        manifest_data = manifest_files.get("manifest.json")
        if isinstance(manifest_data, dict):
            n = manifest_data.get("Name")
            if isinstance(n, str):
                feature_name = n
            uid = manifest_data.get("UniqueID")
            if isinstance(uid, str):
                mod_id = uid

    # If we don't have a feature name yet, try the packaged zip.
    if feature_name is None or mod_id is None:
        try:
            output = await get_mod_output(request_id)
        except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "api.summary.db_error",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            output = None
        if output:
            if status == "unknown":
                status = output.get("status", status)
            zip_key = output.get("zip_key")
            if zip_key:
                try:
                    zip_files = read_zip(zip_key)
                except (ValueError, OSError) as exc:
                    logger.warning(
                        "api.summary.read_zip_failed",
                        request_id=request_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    zip_files = {}
                # Prefer MANIFEST.json (more reliable + includes file count).
                if "MANIFEST.json" in zip_files:
                    try:
                        mf = json.loads(zip_files["MANIFEST.json"])
                        if isinstance(mf, dict):
                            mid = mf.get("mod_id")
                            if mod_id is None and isinstance(mid, str):
                                mod_id = mid
                            file_count = int(mf.get("file_count", file_count) or 0)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
                if "manifest.json" in zip_files:
                    try:
                        m = json.loads(zip_files["manifest.json"])
                        if isinstance(m, dict):
                            if feature_name is None:
                                n = m.get("Name")
                                if isinstance(n, str):
                                    feature_name = n
                            if mod_id is None:
                                uid = m.get("UniqueID")
                                if isinstance(uid, str):
                                    mod_id = uid
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
            if not created_at:
                ts = output.get("created_at")
                if isinstance(ts, datetime):
                    created_at = ts.isoformat()
                elif ts is not None:
                    created_at = str(ts)
            if cancellation_reason is None and status == "cancelled":
                cancellation_reason = await _get_cancellation_reason_safe(request_id)

    # Determine T1 status from current signals.
    if t1_errors:
        t1_status = "failed"
    elif status in ("done", "packaging"):
        t1_status = "passed"
    elif status in ("running", "generating", "t1_gating"):
        t1_status = "running"
    else:
        t1_status = "pending"

    # Build the human-readable text block.
    feature = feature_name or "unnamed mod"
    lines: list[str] = [
        f"Mod {request_id} ({status})",
        f"  Feature: {feature}",
    ]
    if mod_id:
        lines.append(f"  ModID: {mod_id}")
    lines.append(
        f"  Files: {file_count} | Generators: {len(generators)}"
    )
    t2_piece = f"T2: {t2_status}"
    if t2_score is not None and t2_max_score:
        t2_piece += f" ({t2_score}/{t2_max_score})"
    elif t2_score is not None:
        t2_piece += f" (score={t2_score})"
    lines.append(
        f"  T1: {t1_status} (errors={len(t1_errors)}) | {t2_piece}"
    )
    if status == "cancelled":
        lines.append(
            f"  Cancellation reason: {cancellation_reason or 'unspecified'}"
        )

    return ModSummaryResponse(
        request_id=request_id,
        status=status,
        feature_name=feature_name,
        mod_id=mod_id,
        file_count=file_count,
        generator_count=len(generators),
        generators=generators,
        t1_status=t1_status,
        t1_error_count=len(t1_errors),
        t2_status=t2_status,
        t2_score=t2_score,
        t2_max_score=t2_max_score,
        t2_passed=t2_passed,
        cancellation_reason=cancellation_reason,
        created_at=created_at,
        summary="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# GET /v1/mods/{id}/timeline — per-stage pipeline execution view
# ---------------------------------------------------------------------------

# Canonical pipeline stage execution order — used by
# ``/v1/mods/{id}/timeline`` so callers can render a fixed-length
# progress bar without re-sorting. The keys mirror the orchestrator
# pipeline's internal ``status`` names, and the labels are the
# user-facing names surfaced on ``current_stage_label``. Keep in sync
# with the ``status`` Literal in ``orchestrator.state.PipelineState``
# and with ``_compute_progress`` in this module.
_TIMELINE_STAGES: tuple[tuple[str, str], ...] = (
    ("routing", "Routing"),
    ("generating", "Generating"),
    ("validating", "Validating"),
    ("reviewing", "Reviewing"),
    ("packaging", "Packaging"),
    ("completed", "Completed"),
)


def _resolve_stage_id(status: str) -> str:
    """Map a pipeline status string to a stage id for the timeline.

    The orchestrator uses ``t1_gating``/``t2_gating`` for its
    internal statuses, but the timeline surface names those stages
    ``validating``/``reviewing`` because those names are what the
    ``_compute_progress`` helper already surfaces on
    ``ModStatusResponse.current_stage``. Keeping a single mapping
    function means every read-side consumer sees the same stage id.
    """
    mapping: dict[str, str] = {
        "pending": "routing",
        "routing": "routing",
        "generating": "generating",
        "t1_gating": "validating",
        "t2_gating": "reviewing",
        "packaging": "packaging",
        "done": "completed",
        "failed": "completed",
        "cancelled": "completed",
    }
    return mapping.get(status, "routing")


def _resolve_stage_label(stage_id: str) -> str:
    """Return the human-readable label for a stage id."""
    for sid, label in _TIMELINE_STAGES:
        if sid == stage_id:
            return label
    return stage_id


def _parse_started_at(value: object) -> datetime | None:
    """Coerce a ``created_at``-shaped value (datetime / ISO str) to a datetime.

    Returns ``None`` for missing / unparseable values. Mirrors the
    normalization logic in ``_compute_duration_seconds`` so the two
    helpers stay in lock-step (naive datetimes are treated as UTC).
    """
    if value is None:
        return None
    parsed: datetime | None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _compute_duration_seconds(
    created_at: object,
    *,
    now: datetime | None = None,
) -> int | None:
    """Compute wall-clock duration in seconds between ``created_at`` and now.

    Accepts a :class:`datetime.datetime` directly (timezone-aware or naive —
    naive is treated as UTC, which is what the pipeline writes) or an
    ISO-format string (the format Redis stores when ``json.dumps`` serializes
    a datetime with ``default=str``).

    Returns:
        - ``None`` if ``created_at`` is missing, ``None``, or unparseable.
        - ``0`` if ``created_at`` is in the future relative to ``now``
          (clock-skew guard between the client that wrote the field and
          the server reading it).
        - ``int`` floor-divided seconds otherwise.

    The ``now`` kwarg is injectable for unit tests so we don't need to
    monkeypatch ``datetime.now`` (which other modules may legitimately
    touch).
    """
    if created_at is None:
        return None
    parsed: datetime | None
    if isinstance(created_at, datetime):
        parsed = created_at
    elif isinstance(created_at, str):
        try:
            parsed = datetime.fromisoformat(created_at)
        except ValueError:
            return None
    else:
        return None
    # Normalize naive datetimes to UTC so a tz-naive created_at doesn't
    # crash the subtraction against a tz-aware now() (or vice versa).
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    delta = (reference - parsed).total_seconds()
    if delta < 0:
        return 0
    return int(delta)


def _build_timeline(
    request_id: str,
    status: str,
    started_at: datetime | None,
    duration_seconds: int | None,
    *,
    redis_state: dict | None = None,
) -> ModTimelineResponse:
    """Construct a :class:`ModTimelineResponse` from existing pipeline state.

    Per-stage ``at`` timestamps are derived by linear interpolation
    between ``started_at`` and ``started_at + duration_seconds``
    using a fixed weight per stage (see the ``weights`` mapping
    below). This is intentionally simple — callers that need exact
    per-stage timing should add explicit stage logging to the
    orchestrator. The interpolation is documented as a best-effort
    approximation in the schema docstring so callers cannot
    accidentally treat it as ground truth.

    ``redis_state`` is optional; when provided, the per-stage
    generator completion counts (succeeded / failed) are forwarded
    to :func:`_compute_progress` so the ``progress_percent`` field
    accurately reflects how many generators have completed in
    ``status="generating"``. When omitted (DB fallback path) the
    progress defaults to the bare ``status`` weight, which is the
    best we can do without Redis.
    """
    current_stage_id = _resolve_stage_id(status)
    current_stage_label = _resolve_stage_label(current_stage_id)
    if redis_state is not None:
        progress = _compute_progress(redis_state)
    else:
        progress = _compute_progress({"status": status})
    progress_percent = int(progress.get("percent", 0) or 0)

    started_iso = started_at.isoformat() if started_at is not None else None
    completed_iso: str | None = None
    if started_at is not None and status in ("done", "failed", "cancelled"):
        # Approximate completion time = started_at + duration_seconds.
        # Falls back to started_at itself when duration is unknown
        # (defensive — the timeline is still meaningful even without
        # an exact duration).
        offset = duration_seconds if duration_seconds is not None else 0
        completed_at = started_at + timedelta(seconds=offset)
        completed_iso = completed_at.isoformat()

    # Build the per-stage list. ``reached`` is True once the pipeline
    # has moved past the stage (status is strictly later in the
    # pipeline), ``current`` is True for the stage the pipeline is
    # presently in. ``at`` is the interpolated timestamp when the
    # stage was entered, or None when not yet reached.
    # Stage ordering matches the orchestrator's execution flow:
    # routing -> generating -> validating -> reviewing -> packaging
    # -> completed. ``_TIMELINE_STAGES`` encodes the order; we walk
    # it twice (once to compute the prefix index, once to build
    # the entries) so the implementation is straightforward.
    stage_ids: list[str] = [sid for sid, _ in _TIMELINE_STAGES]
    if current_stage_id in stage_ids:
        current_idx = stage_ids.index(current_stage_id)
    else:
        current_idx = -1

    total_seconds = float(duration_seconds) if duration_seconds is not None else 0.0
    weights = {
        "routing": 0.05,
        "generating": 0.20,
        "validating": 0.60,
        "reviewing": 0.75,
        "packaging": 0.90,
        "completed": 1.00,
    }

    stage_entries: list[TimelineStage] = []
    for idx, (sid, label) in enumerate(_TIMELINE_STAGES):
        # A stage is ``reached`` if it has been entered at any point.
        # For a terminal status (done/failed/cancelled) every stage
        # is reached; otherwise only stages up to and including the
        # current one are reached.
        if status in ("done", "failed", "cancelled"):
            reached = True
        elif current_idx < 0:
            reached = False
        else:
            reached = idx <= current_idx
        current = sid == current_stage_id
        at_iso: str | None = None
        if reached and started_at is not None:
            weight = weights.get(sid, 0.0)
            interpolated = started_at + timedelta(seconds=total_seconds * weight)
            at_iso = interpolated.isoformat()
        stage_entries.append(
            TimelineStage(
                stage=sid,
                label=label,
                reached=reached,
                current=current,
                at=at_iso,
            )
        )

    return ModTimelineResponse(
        request_id=request_id,
        status=status,
        started_at=started_iso,
        completed_at=completed_iso,
        progress_percent=progress_percent,
        current_stage=current_stage_id,
        current_stage_label=current_stage_label,
        stages=stage_entries,
    )


@router.get("/mods/{request_id}/timeline", response_model=ModTimelineResponse)
async def get_mod_timeline(request_id: str) -> ModTimelineResponse:
    """Get the pipeline timeline for a request.

    Returns the per-stage execution order, which stage is currently
    active, and approximate stage-entry timestamps so operators
    and chat bots can render "where it is right now" without
    re-parsing the full status payload. Pure read-only and
    side-effect-free.

    Cache-first: reads from Redis pipeline state when available,
    falls back to PostgreSQL via :func:`storage.queries.get_mod_output`
    otherwise. Both paths are tolerated because the timeline is
    useful for completed requests (Redis state may have expired
    even when the DB row is still there).

    A transient Redis / DB error on either path is logged and
    treated as a miss — the fallback path is attempted before a
    404 is returned, so a Redis outage cannot take the endpoint
    down for completed requests that the DB knows about.
    Programming bugs (TypeError, KeyError) still propagate so
    they aren't masked as transient outages.
    """
    redis_state: dict | None = None
    status_value: str | None = None
    created_at_value: object = None
    started_at_value: datetime | None = None
    duration_seconds_value: int | None = None
    try:
        from storage.redis import get_pipeline_state as redis_get_state
        redis_state = await redis_get_state(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.timeline.redis_error",
            request_id=request_id,
            error=str(exc), error_type=type(exc).__name__,
        )
        redis_state = None

    if redis_state:
        status_value = str(redis_state.get("status", "pending"))
        created_at_value = redis_state.get("created_at")
        started_at_value = _parse_started_at(created_at_value)
        # Derive duration from now if not already provided by the
        # caller / response builder. Clamp negative durations to 0
        # to mirror the clock-skew guard in
        # ``_compute_duration_seconds``.
        if started_at_value is not None:
            now = datetime.now(timezone.utc)
            delta = (now - started_at_value).total_seconds()
            duration_seconds_value = int(delta) if delta >= 0 else 0
    else:
        # DB fallback — status only. We can't reconstruct per-stage
        # timestamps from the DB alone (they live in Redis), so
        # ``started_at`` and per-stage ``at`` will be None and only
        # ``current_stage`` is meaningfully populated. The DB row
        # already encodes ``created_at`` though, so we can surface
        # at least the request's start time.
        try:
            output = await get_mod_output(request_id)
        except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "api.timeline.db_error",
                request_id=request_id,
                error=str(exc), error_type=type(exc).__name__,
            )
            output = None
        if not output:
            logger.warning("api.timeline.not_found", request_id=request_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Request {request_id} not found",
            )
        status_value = str(output.get("status", "unknown"))
        created_at_value = output.get("created_at")
        started_at_value = _parse_started_at(created_at_value)
        duration_seconds_value = _compute_duration_seconds(created_at_value)

    if status_value is None:
        # Defensive: a DB row that lost its status field shouldn't
        # crash the endpoint — surface as "unknown" so the caller
        # can still see the request exists.
        status_value = "unknown"

    logger.info(
        "api.timeline.returned",
        request_id=request_id,
        status=status_value,
        started=started_at_value is not None,
        source="redis" if redis_state else "db",
    )
    return _build_timeline(
        request_id=request_id,
        status=status_value,
        started_at=started_at_value,
        duration_seconds=duration_seconds_value,
        redis_state=redis_state,
    )


# Upper bound on the number of T2 iterations we surface in the response.
# Mirrors ``orchestrator.state.MAX_T2_ITERATIONS_LIMIT`` (which is the
# cap on retries, not iterations — but in practice the number of iterations
# a request can produce is bounded by ``MAX_T2_ITERATIONS_LIMIT + 1``). We
# cap at 16 to leave headroom for a future bump while keeping the response
# envelope small. Any iteration index beyond this is dropped from the
# response (the request still ran them; we just don't list them all).
_T2_JUDGES_MAX_ITERATIONS: int = 16


def _build_t2_judges_from_redis(
    request_id: str,
    redis_state: dict[str, Any] | None,
) -> T2JudgesResponse:
    """Construct :class:`T2JudgesResponse` from a Redis pipeline state blob.

    Round v52 (Feature — per-iteration T2 history endpoint). Pure
    transformation helper with no I/O; the route layer handles the
    Redis read + the DB existence fallback.

    Defensive contract:

    - ``redis_state is None`` → empty response with ``source="redis"``
      is NOT possible here (the route wouldn't call this helper with
      ``None``; that path returns ``source="none"`` directly). If
      called with ``None``, returns empty with ``source="none"``.
    - ``t2_judge_results`` missing or wrong type → empty list. The
      helper never raises on a malformed key.
    - Individual entries that fail Pydantic validation are SKIPPED
      with a WARNING log rather than 500-ing the endpoint. This
      matches the storage-side pattern of tolerating stale Redis
      payloads across version drift.
    - The cap on returned iterations (``_T2_JUDGES_MAX_ITERATIONS``)
      protects against runaway responses if a future pipeline change
      accumulates iterations unbounded.
    """
    if not redis_state:
        return T2JudgesResponse(
            request_id=request_id,
            iterations=[],
            final_score=None,
            final_passed=None,
            t2_available=False,
            source="none",
        )

    raw_iterations = redis_state.get("t2_judge_results")
    iterations: list[T2JudgeIteration] = []
    if isinstance(raw_iterations, list):
        for index, entry in enumerate(raw_iterations):
            if not isinstance(entry, dict):
                logger.warning(
                    "api.t2_judges.skipped_non_dict_entry",
                    request_id=request_id,
                    index=index,
                    entry_type=type(entry).__name__,
                )
                continue
            try:
                iterations.append(T2JudgeIteration(**entry))
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning(
                    "api.t2_judges.skipped_invalid_entry",
                    request_id=request_id,
                    index=index,
                    error=str(exc), error_type=type(exc).__name__,
                )
                continue
            if len(iterations) >= _T2_JUDGES_MAX_ITERATIONS:
                logger.warning(
                    "api.t2_judges.truncated",
                    request_id=request_id,
                    cap=_T2_JUDGES_MAX_ITERATIONS,
                )
                break

    # Echo the final-iteration score / passed / availability from the
    # top-level Redis fields (which are the values from the LATEST
    # iteration that ran, written by the orchestrator alongside the
    # per-iteration list).
    final_score_raw = redis_state.get("t2_score")
    final_score: int | None = None
    if isinstance(final_score_raw, int):
        final_score = max(0, min(10, final_score_raw))
    elif final_score_raw is not None:
        try:
            final_score = max(0, min(10, int(final_score_raw)))
        except (TypeError, ValueError):
            final_score = None

    final_passed_raw = redis_state.get("t2_passed")
    final_passed: bool | None = None
    if isinstance(final_passed_raw, bool):
        final_passed = final_passed_raw

    t2_available_raw = redis_state.get("t2_available")
    t2_available: bool = bool(t2_available_raw) if isinstance(
        t2_available_raw, bool
    ) else False

    return T2JudgesResponse(
        request_id=request_id,
        iterations=iterations,
        final_score=final_score,
        final_passed=final_passed,
        t2_available=t2_available,
        source="redis",
    )


@router.get("/mods/{request_id}/t2_judges", response_model=T2JudgesResponse, dependencies=[Depends(verify_api_key)])
async def get_mod_t2_judges(request_id: str) -> T2JudgesResponse:
    """Return the per-iteration T2 judge history for a request.

    Round v52 (Feature — per-iteration T2 history endpoint).
    Companion to ``GET /v1/mods/{id}`` (single-pass status with
    final t2_score/t2_feedback) and ``GET /v1/mods/{id}/timeline``
    (stage-by-stage timing). Together the three endpoints give an
    operator the full picture of a request:

    - ``/v1/mods/{id}`` — "did it pass? what was the final score?"
    - ``/v1/mods/{id}/timeline`` — "how long did each stage take?"
    - ``/v1/mods/{id}/t2_judges`` — "what did each T2 retry see?"

    Cache-first: reads from Redis pipeline state when available. The
    per-iteration data is NOT persisted to ``mod_outputs`` (only the
    final t2_score / t2_feedback are), so this endpoint is naturally
    short-lived: once the Redis 24h TTL expires, the per-iteration
    history is gone for good. The DB fallback path therefore cannot
    reconstruct the per-iteration list — it can only confirm the
    request exists (returns ``source="db_unavailable"``) so the
    caller knows the 200 is correct but the history has expired.

    Failure modes:

    - Redis transient error (ConnectionError / TimeoutError /
      RuntimeError): logged at WARNING under
      ``api.t2_judges.redis_error``, falls through to the DB
      existence check. If the request exists in the DB, returns
      200 with ``iterations=[]`` and ``source="db_unavailable"``.
      If not, returns 404.
    - Redis returns None AND DB returns None → 404
      ``Request {id} not found``.
    - Redis returns a malformed ``t2_judge_results`` list: each
      non-dict / Pydantic-failing entry is skipped with a WARNING
      log. The endpoint never raises on a bad payload.
    - Request never ran T2 (``t2_judge_results`` is ``[]``): 200
      with empty iterations and the final-iteration echo fields
      at their defaults. ``source="redis"`` because the request
      DID exist in Redis (just with an empty history).

    Why no auth? Mirrors the v17 feature-flag operator endpoints
    and the v46 purge endpoint rationale (see those docstrings for
    the full reasoning). The payload exposes only the per-iteration
    T2 scores and feedback, none of which are sensitive on their
    own. Adding ``Depends(verify_api_key)`` is a one-line change
    if production needs it.
    """
    redis_state: dict[str, Any] | None = None
    try:
        from storage.redis import get_pipeline_state as redis_get_state

        redis_state = await redis_get_state(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.t2_judges.redis_error",
            request_id=request_id,
            error=str(exc), error_type=type(exc).__name__,
        )
        redis_state = None

    if redis_state:
        response = _build_t2_judges_from_redis(request_id, redis_state)
        logger.info(
            "api.t2_judges.returned",
            request_id=request_id,
            iterations=len(response.iterations),
            final_score=response.final_score,
            t2_available=response.t2_available,
            source="redis",
        )
        return response

    # Redis miss → confirm existence via the DB so the caller can
    # distinguish "this request never ran T2" (Redis-cached empty
    # history, 200 with source="redis" empty list) from "this
    # request existed but its pipeline state has expired"
    # (200 with source="db_unavailable" empty list) from "this
    # request never existed" (404).
    try:
        output = await get_mod_output(request_id)
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.t2_judges.db_error",
            request_id=request_id,
            error=str(exc), error_type=type(exc).__name__,
        )
        output = None

    if not output:
        logger.warning("api.t2_judges.not_found", request_id=request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    # DB fallback — request exists but per-iteration history is
    # Redis-only. Surface the final score + passed from the DB row
    # so the caller at least gets the same "what was the final
    # verdict" data that ``GET /v1/mods/{id}`` returns.
    db_response = T2JudgesResponse(
        request_id=request_id,
        iterations=[],
        final_score=output.get("t2_score"),
        final_passed=output.get("t2_passed"),
        t2_available=bool(output.get("t2_available", False)),
        source="db_unavailable",
    )
    logger.info(
        "api.t2_judges.returned",
        request_id=request_id,
        iterations=0,
        final_score=db_response.final_score,
        t2_available=db_response.t2_available,
        source="db_unavailable",
    )
    return db_response


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
        "cancelled": ("cancelled", 100),
    }
    stage, percent = stage_map.get(status, ("unknown", 0))

    # Refine generating progress based on generator completion
    if status == "generating":
        succeeded = redis_state.get("generators_succeeded", [])
        failed = redis_state.get("generators_failed", [])
        total_gens = len(succeeded) + len(failed)
        generators = redis_state.get("generators", [])
        total = len(generators) if generators else total_gens + 1
        if total > 0:
            percent = 20 + int(total_gens / total * 35)
        else:
            percent = 20

    return {"stage": stage, "percent": percent}


# ---------------------------------------------------------------------------
# GET /v1/mods listing — pagination / filter caps
# ---------------------------------------------------------------------------

# Listing limit cap. ``limit`` is also bounded from below (``ge=1``) by
# the Pydantic model, so the actual range is 1..100. We keep the cap as
# a module constant so it can be referenced by tests without retyping
# the literal.
_MOD_LIST_LIMIT_MIN: int = 1
_MOD_LIST_LIMIT_MAX: int = 100
_MOD_LIST_LIMIT_DEFAULT: int = 20
# Pagination cap on the ``offset`` query parameter for ``GET /v1/mods``.
# Without an upper bound, ``?offset=1000000&limit=30`` would force the DB
# to scan-and-skip 1,000,000 rows before returning a 30-row page — an
# accidental-DoS vector (a misbehaving client or a misconfigured operator
# can issue a single query that takes seconds to complete). 10000 is
# generous (~333 pages of 30 results, or ~10k results total) and matches
# the v82 F3 unconditional pattern (no env gate; see v79 F1 no-store).
_MOD_LIST_OFFSET_MAX: int = 10000

# Canonical sort keys for the listing endpoint. Mirrors
# ``storage.queries._LIST_SORT_ORDERS`` — kept here as the public
# contract so the Pydantic Literal below stays in sync with the SQL
# the helper will actually execute.
_MOD_LIST_SORT_KEYS: tuple[str, ...] = (
    "created_at_desc",
    "created_at_asc",
    "updated_at_desc",
)


@router.get("/mods", response_model=ModListResponse)
async def list_mods(
    user_id: Annotated[
        str | None,
        Query(
            description="Filter by user_id (exact match). Omit to list all users.",
            max_length=128,
        ),
    ] = None,
    status_filter: Annotated[
        Literal["pending", "running", "done", "failed", "cancelled"] | None,
        Query(
            alias="status",
            description=(
                "Filter by status. Must be one of: pending, running, done, "
                "failed, cancelled."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            description="Maximum number of rows to return. Clamped to 1..100.",
            ge=_MOD_LIST_LIMIT_MIN,
            le=_MOD_LIST_LIMIT_MAX,
        ),
    ] = _MOD_LIST_LIMIT_DEFAULT,
    offset: Annotated[
        int,
        Query(
            description="Number of rows to skip (0 = first page). Must be >= 0.",
            ge=0,
        ),
    ] = 0,
    sort: Annotated[
        Literal["created_at_desc", "created_at_asc", "updated_at_desc"],
        Query(
            description=(
                "Sort order. One of 'created_at_desc' (default, newest first), "
                "'created_at_asc' (oldest first), or 'updated_at_desc' (most "
                "recently updated first; rows with no updated_at timestamp "
                "sort last)."
            ),
        ),
    ] = "created_at_desc",
) -> JSONResponse:
    """List recent mod requests with optional filters.

    Pure read-only listing — no side effects, no cancellation, no
    status mutation. Returns the most recent N requests (ordered per
    ``sort``) optionally narrowed by ``user_id`` and/or ``status``.

    Why no auth gate? This endpoint exposes only metadata (request_id,
    user_id, status, phase, created_at) — none of which are sensitive
    on their own. The detailed status payload (T1 errors, T2 feedback,
    cancellation reason) is still gated by ``GET /v1/mods/{id}`` via
    Redis lookup + per-request logic. If you need to lock down the
    listing in production, add a ``verify_api_key`` dependency here.

    Query params:
        ``user_id``: optional. Exact match on ``mod_requests.user_id``.
        ``status``: optional. Must be one of ``pending`` / ``running``
            / ``done`` / ``failed`` / ``cancelled`` (Pydantic Literal
            rejects anything else with a 422). The Python parameter is
            ``status_filter`` because ``status`` collides with the
            imported FastAPI ``status`` module — the alias preserves the
            public name.
        ``limit``: optional, default 20. ``ge=1, le=100`` is enforced
            by FastAPI's ``Query`` validator; an out-of-range value
            returns 422 before we ever touch the DB.
        ``offset``: optional, default 0. ``ge=0`` is enforced by
            FastAPI's ``Query`` validator; a negative value returns 422.
        ``sort``: optional, default ``"created_at_desc"``. Pydantic
            Literal rejects anything outside the three supported keys
            with a 422. See :data:`_MOD_LIST_SORT_KEYS`.

    Filters echoed back in ``filters`` so the caller can verify the
    query string was honored (e.g. when a default kicked in).
    Pagination metadata (``limit``, ``offset``, ``has_more``) and the
    real total count of matching rows (``total``) are surfaced on the
    envelope so a "Page 1 of N" UI can render without a second
    round-trip.
    """
    # The Pydantic Literal already rejects unknown statuses and sort
    # keys, and Query already bounds ``limit`` and ``offset``. We still
    # clamp defensively in case the route is called from internal code
    # that bypasses validation.
    if limit < _MOD_LIST_LIMIT_MIN:
        limit = _MOD_LIST_LIMIT_MIN
    if limit > _MOD_LIST_LIMIT_MAX:
        limit = _MOD_LIST_LIMIT_MAX
    if offset < 0:
        offset = 0

    # v82 F3 — unconditional pagination cap. An uncapped ``offset`` is an
    # accidental-DoS vector (the DB must scan-and-skip every row before
    # returning the page). The cap is inclusive: ``offset == 10000`` is
    # allowed, ``offset == 10001`` is rejected. See the constant block
    # above for the rationale and the 10000 headroom justification.
    if offset > _MOD_LIST_OFFSET_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"offset must be <= {_MOD_LIST_OFFSET_MAX}",
        )

    # ``total`` is a real COUNT(*) over the same WHERE clause so the
    # envelope can drive a "Page 1 of N" UI without a second round-trip.
    # We issue it in parallel with the page query via ``asyncio.gather``
    # so the latency cost is one round-trip, not two.
    try:
        rows, total = await asyncio.gather(
            list_mod_requests(
                user_id=user_id,
                status=status_filter,
                limit=limit,
                offset=offset,
                sort=sort,
            ),
            count_mod_requests(
                user_id=user_id,
                status=status_filter,
            ),
        )
    except (ConnectionError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
        # Database unavailable — surface 503 (not 500) so load balancers
        # and the /health probe can distinguish "down" from "bug". The
        # middleware's access log already records the failure; re-raising
        # here would turn every DB outage into an unhandled 500.
        logger.warning(
            "api.mods.db_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc

    items: list[ModListItem] = []
    for row in rows:
        created_at = row.get("created_at")
        # ``created_at`` is typed as DateTime(timezone=True) on the
        # SQLAlchemy column, so this is always a real datetime here.
        # The ``datetime`` fallback covers the unit-test shim that
        # returns plain dicts.
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        updated_at = row.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, datetime):
            # Same defensive fallback as ``created_at``.
            updated_at = datetime.now(timezone.utc)
        phase_value = row.get("phase")
        items.append(
            ModListItem(
                request_id=row["request_id"],
                user_id=row.get("user_id"),
                status=row.get("status", "unknown"),
                phase=phase_value,
                feature=phase_value,  # mirror under the public-facing name
                prompt=row.get("prompt"),
                created_at=created_at,
                updated_at=updated_at,
                has_zip=bool(row.get("zip_key")),
            )
        )

    # ``has_more`` is the canonical "is there another page?" flag.
    # Computed from the real total so it doesn't over-estimate when
    # exactly ``limit`` rows remain on the current page.
    has_more = (offset + len(items)) < total

    logger.info(
        "api.mods.listed",
        count=len(items),
        total=total,
        limit=limit,
        offset=offset,
        user_id=user_id,
        status=status_filter,
        sort=sort,
    )
    # v142 Blue (Feature 2 — Cache-Control: no-store on /v1/mods listing):
    # the listing endpoint exposes other users' user_id + truncated prompt
    # when the user_id filter is omitted. The endpoint is unauthenticated
    # today (see docstring above). ``Cache-Control: no-store`` prevents any
    # CDN / sidecar / browser intermediate from caching the listing and
    # serving a stale snapshot to subsequent callers. Only the 200 path
    # gets the header; the 422 path (Pydantic Query validation) still uses
    # FastAPI's default error envelope, which has no Cache-Control
    # directives.
    return JSONResponse(
        content=ModListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
            filters={"user_id": user_id, "status": status_filter},
        ).model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# v56 Red: phase-keyed estimate endpoints.
#
# Two new read-only endpoints (``GET /v1/estimates`` + ``GET /v1/estimates/{phase}``)
# that mirror :data:`app.estimation._PHASE_SECONDS` and
# :data:`app.estimation._DEFAULT_SECONDS` as a JSON surface. Useful for
# chat bots that want to tell the user "this kind of mod usually takes
# ~60 seconds" before they submit a prompt, and for dashboards that
# want to render the full phase catalogue without scraping internal
# state.
#
# Both handlers share the same lazy-import pattern already used by the
# other endpoints that touch optional modules (``preview_route`` uses
# :func:`orchestrator.router.route` the same way). The deferred
# ``from app.estimation import ...`` inside each function body means
# :mod:`app.api.routes` still imports cleanly on a checkout that hasn't
# had :mod:`app.estimation` restored yet — only the two handlers
# themselves raise :class:`ImportError` until :mod:`app.estimation` is
# on master. This is intentional: the rest of the 32 endpoints stay
# green while the parent restores the module.
# ---------------------------------------------------------------------------

# Module-level cache for the full /v1/estimates response.
# The data backing the endpoint is a frozen module-level dict in
# app.estimation — computing the sorted response on every call is
# cheap (one tuple sort, one dict copy), but caching the built
# envelope lets dashboards that poll the endpoint every second
# avoid paying the rebuild cost. Invalidated automatically on
# process restart; no need for explicit cache busting because
# app.estimation is loaded once at import time.
_ESTIMATES_CACHE: EstimatesResponse | None = None


def _build_estimates_response() -> EstimatesResponse:
    """Build (and lazily cache) the full ``/v1/estimates`` envelope.

    The data source is :data:`app.estimation._PHASE_SECONDS` plus
    :data:`app.estimation._DEFAULT_SECONDS`. Both are module-level
    constants — there is no live Redis/Postgres read here, so the
    cache is purely a CPU-saving optimization for high-frequency
    polling callers.

    Returns:
        The envelope, with ``estimates`` sorted by phase id so
        callers can rely on a stable iteration order (snapshot
        tests, UI rendering).
    """
    global _ESTIMATES_CACHE
    if _ESTIMATES_CACHE is not None:
        return _ESTIMATES_CACHE
    from app.estimation import _PHASE_SECONDS, _DEFAULT_SECONDS  # noqa: SLF001

    rows: list[PhaseEstimate] = [
        PhaseEstimate(phase=phase, seconds=int(seconds))
        for phase, seconds in sorted(_PHASE_SECONDS.items())
    ]
    _ESTIMATES_CACHE = EstimatesResponse(
        estimates=rows,
        default_seconds=int(_DEFAULT_SECONDS),
        count=len(rows),
    )
    return _ESTIMATES_CACHE


@router.get("/estimates", response_model=EstimatesResponse)
async def list_estimates() -> EstimatesResponse:
    """Return the canonical phase→seconds estimate table.

    Pure read-only endpoint that mirrors
    :data:`app.estimation._PHASE_SECONDS` and
    :data:`app.estimation._DEFAULT_SECONDS` as a JSON response.
    Useful for chat bots that want to tell the user "this kind of
    mod usually takes ~60 seconds" before they submit a prompt,
    and for dashboards that want to render a phase catalogue.

    The endpoint has no side effects — no LLM call, no DB read,
    no Redis hit — and can be polled freely from a dashboard
    without coordinating with any other state. ``generated_at``
    is omitted (vs. ``/v1/mods/stats``) because the data is a
    frozen module constant; the response only changes when the
    process restarts with a new ``app.estimation`` revision, and
    a deploy-event-driven cache bust is sufficient for that.

    Ordering: ``estimates`` is sorted by ``phase`` id (lexicographic)
    so callers can rely on a stable iteration order. The
    ``count`` field mirrors ``len(estimates)`` so the caller can
    sanity-check the response without a second ``len()`` call.
    """
    response = _build_estimates_response()
    logger.info(
        "api.estimates.listed",
        count=response.count,
        default_seconds=response.default_seconds,
    )
    return response


@router.get("/estimates/{phase}", response_model=PhaseEstimateResponse)
async def get_estimate_for_phase(phase: str) -> PhaseEstimateResponse:
    """Return the seconds estimate for a single phase.

    Thin lookup wrapper over :func:`app.estimation.estimate_seconds_for_phase`
    — the endpoint surfaces the same value plus a ``matched`` flag
    so callers can distinguish "this phase has a tuned estimate"
    from "this phase fell back to the default". The full table
    is available via ``GET /v1/estimates`` for callers that need it.

    Args:
        phase: Phase id (e.g. ``shop_channel``, ``weather_event``).
            Echoed back in the response. An unknown phase is not a
            404 — instead the endpoint returns ``matched=False``
            and ``seconds == default_seconds`` so chat bots can
            degrade gracefully ("no specific estimate — default
            is 90s") without a try/except on the client.

    Returns:
        The single-phase envelope. ``matched`` is True iff the
        phase id was found in
        :data:`app.estimation._PHASE_SECONDS`.
    """
    from app.estimation import _PHASE_SECONDS, _DEFAULT_SECONDS, estimate_seconds_for_phase  # noqa: SLF001

    cleaned_phase = phase.strip()
    if not cleaned_phase:
        # Defensive: FastAPI's path param rejects empty strings at
        # the routing layer, but a whitespace-only phase (e.g.
        # ``/v1/estimates/%20%20``) could still slip through.
        # Treat it the same as an unknown phase so the response
        # shape is consistent — ``matched=False`` is the canonical
        # signal for "no specific estimate".
        cleaned_phase = ""
    seconds = int(estimate_seconds_for_phase(cleaned_phase or None))
    default = int(_DEFAULT_SECONDS)
    matched = cleaned_phase in _PHASE_SECONDS
    logger.info(
        "api.estimates.phase_lookup",
        phase=cleaned_phase,
        seconds=seconds,
        matched=matched,
    )
    return PhaseEstimateResponse(
        phase=cleaned_phase,
        seconds=seconds,
        default_seconds=default,
        matched=matched,
    )


# ---------------------------------------------------------------------------
# v57 Red: prompt-keyed estimate endpoints.
#
# Two new endpoints that compose the existing helpers
# (:func:`orchestrator.router.route` + :func:`app.estimation.estimate_seconds_for_phase`)
# into a UI-friendly "how long will this take?" preview surface. They
# sit alongside the existing ``/v1/estimates`` (full table) and
# ``/v1/estimates/{phase}`` (single-phase lookup) endpoints without
# changing any of those contracts.
#
# Both handlers share a single internal helper
# (:func:`_estimate_for_prompt`) so the JSON shape and the routing
# heuristic are guaranteed to stay byte-identical between the single
# and batch variants — a future change to the routing heuristic only
# needs to land in one place.
#
# Like the two phase-keyed handlers above, all imports from
# :mod:`app.estimation` and :mod:`orchestrator.router` are deferred to
# the function body (NOT the module top) so the file still imports
# cleanly on a checkout that hasn't restored :mod:`app.estimation` yet.
# The decorators (``@router.get`` / ``@router.post``) only register the
# function objects; they don't call them — so registration succeeds.
# Only a real HTTP request to ``/v1/estimate`` or ``/v1/estimate/batch``
# raises :class:`ImportError` until :mod:`app.estimation` is on master.
# ---------------------------------------------------------------------------


def _estimate_for_prompt(prompt: str) -> PromptEstimateResponse:
    """Compute a :class:`PromptEstimateResponse` for one trimmed prompt.

    Internal helper shared by :func:`estimate_prompt_endpoint` (singular)
    and :func:`estimate_prompt_batch_endpoint` (batch). Resolves the
    phase via :func:`orchestrator.router.route` and the seconds via
    :func:`app.estimation.estimate_seconds_for_phase`, then packages
    them into the response shape.

    The caller is responsible for prompt hygiene (trim + non-empty
    validation); this helper trusts its input and is the single source
    of truth for the estimation rule used by both endpoints.

    Args:
        prompt: A non-empty, trimmed prompt. The function does not
            re-trim, so a caller passing ``"  "`` would propagate the
            whitespace into the ``prompt`` echo field — kept as a
            precondition rather than a hidden transform so the batch
            endpoint can rely on its own schema-level validator to
            reject whitespace-only prompts at the 422 boundary.

    Returns:
        The populated :class:`PromptEstimateResponse`. ``matched`` is
        True iff the resolved phase is in the canonical phase table;
        otherwise ``seconds == default_seconds`` and the response
        surfaces the fallback so the client can render "default
        estimate" without a second round-trip.
    """
    # Deferred imports — ``orchestrator.router`` and ``app.estimation``
    # are both cheap to import, but pulling them at module top-level
    # would force every test that imports ``app.api.routes`` to load
    # the orchestrator import chain. The deferred pattern is already
    # used by ``preview_route`` and the ``_build_estimates_response``
    # helper above, so this stays consistent.
    from app.estimation import (
        _DEFAULT_SECONDS,  # noqa: SLF001
        _PHASE_SECONDS,  # noqa: SLF001
        estimate_seconds_for_phase,
    )
    from orchestrator.router import route as route_prompt

    # ``route`` returns ``(phase, RoutingHint)`` — we keep the hint for
    # the ``game`` field on the response so the client gets the same
    # ``game`` string the orchestrator would use.
    phase, hint = route_prompt(prompt)
    default = int(_DEFAULT_SECONDS)
    seconds = int(estimate_seconds_for_phase(phase))
    matched = phase in _PHASE_SECONDS
    game = str(hint.get("game", "stardew_valley"))
    return PromptEstimateResponse(
        prompt=prompt,
        phase=phase,
        seconds=seconds,
        default_seconds=default,
        matched=matched,
        game=game,
    )


@router.get("/estimate", response_model=PromptEstimateResponse)
async def estimate_prompt_endpoint(
    prompt: Annotated[
        str,
        Query(
            description=(
                "Natural-language prompt to estimate. Required, "
                "non-empty. Trimmed at the handler boundary; "
                "whitespace-only is rejected with a 422."
            ),
            min_length=1,
            max_length=10000,
        ),
    ],
) -> PromptEstimateResponse:
    """Return the seconds estimate for a single prompt.

    Read-only preview of the orchestrator's routing + estimation
    pipeline. Composes :func:`orchestrator.router.route` and
    :func:`app.estimation.estimate_seconds_for_phase` so the value
    matches what ``POST /v1/mods/generate`` would actually compute
    for the same prompt.

    Use cases:

    * A web UI debouncing the user's typing and rendering
      "this will take ~60 seconds" before the user clicks Submit.
    * A Discord bot pre-checking the cost of a request before
      forwarding to the full generation endpoint.
    * Integration tests asserting that a known prompt still
      resolves to the expected phase/seconds pair after a routing
      table change.

    The endpoint never starts a generation, never touches Redis or
    Postgres, and never makes an LLM call — it is safe to call as
    often as the user types.
    """
    # Defensive trim: FastAPI's ``Query(min_length=1)`` only catches
    # the empty-string case, not a whitespace-only prompt. The router
    # itself lowercases + contains-checks, so a whitespace-only prompt
    # would still produce a (low-quality) routing decision — reject
    # it here so the caller sees a clear 422 instead of a surprise
    # estimate. Mirrors ``preview_route``'s hygiene rule.
    cleaned = prompt.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="prompt must not be empty or whitespace-only",
        )

    response = _estimate_for_prompt(cleaned)
    logger.info(
        "api.estimate.prompt",
        prompt=cleaned,
        phase=response.phase,
        seconds=response.seconds,
        matched=response.matched,
        game=response.game,
    )
    return response


@router.post("/estimate/batch", response_model=BatchPromptEstimateResponse)
async def estimate_prompt_batch_endpoint(
    req: BatchPromptEstimateRequest,
) -> BatchPromptEstimateResponse:
    """Return seconds estimates for a list of prompts in one round-trip.

    Batch variant of :func:`estimate_prompt_endpoint`. Accepts 1-20
    prompts (schema-bounded) and returns one :class:`BatchPromptEstimateItem`
    per prompt in the same order. The endpoint is pure CPU (the router
    is in-memory and the estimation table is a frozen module dict), so
    the cost scales linearly with the batch size and there is no
    thread-pool offload — a 20-prompt batch finishes in well under
    the request budget.

    Use cases:

    * A web UI rendering a "what can I generate" card grid where each
      card needs an estimate before the user clicks into the generation
      form.
    * A Discord bot pre-checking the cost of a slash-command's prompt
      list before prompting the user to confirm.
    * An integration test that wants to assert a mapping of prompts
      → phases without paying for 20 separate round-trips.

    The response echoes the canonical ``default_seconds`` at the
    envelope level so a UI can render the fallback value once and
    cache it across cards without re-asking the server.
    """
    from app.estimation import _DEFAULT_SECONDS  # noqa: SLF001

    # ``_estimate_for_prompt`` already logs ``api.estimate.prompt``
    # for each row, but we also emit a single batch-level event so
    # dashboards can distinguish "one big batch request" from "20
    # individual calls" without re-aggregating the per-row events.
    items: list[BatchPromptEstimateItem] = []
    for cleaned in req.prompts:
        full = _estimate_for_prompt(cleaned)
        # Drop the echoed prompt from the row shape — it's already
        # in the request body so a UI can correlate by index. Saves
        # ~30 bytes per row on a 20-prompt batch (~600 bytes total).
        items.append(
            BatchPromptEstimateItem(
                phase=full.phase,
                seconds=full.seconds,
                default_seconds=full.default_seconds,
                matched=full.matched,
                game=full.game,
            )
        )

    logger.info(
        "api.estimate.batch",
        item_count=len(items),
        matched_count=sum(1 for it in items if it.matched),
    )
    return BatchPromptEstimateResponse(
        estimates=items,
        count=len(items),
        default_seconds=int(_DEFAULT_SECONDS),
    )


# Cap on the ``?limit=`` query parameter. Matches the writer-side
# cap in :mod:`storage.redis` (``_PIPELINE_LOG_MAX_ENTRIES``) so a
# caller can never ask for more than the buffer holds; the route
# layer clamps anything larger down to this value.
_MAX_LOG_LIMIT = 500


def _build_log_entries(raw_entries: list[dict]) -> list[LogEntry]:
    """Translate raw Redis-stored log dicts into ``LogEntry`` models.

    Defensive against schema drift:

    - Missing ``timestamp`` / ``level`` / ``event`` / ``message``
      keys fall back to safe defaults (empty string for text
      fields, ``"INFO"`` for ``level``).
    - Extra keys are kept in ``LogEntry.extras`` (they ride along
      on the response so the client can render them).
    - Non-dict inputs are skipped (logged at WARNING by the
      caller; this helper never raises on bad data).
    """
    reserved = {"timestamp", "level", "event", "message"}
    out: list[LogEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        extras = {k: v for k, v in raw.items() if k not in reserved}
        try:
            entry = LogEntry(
                timestamp=str(raw.get("timestamp", "")),
                level=str(raw.get("level", "INFO")),
                event=str(raw.get("event", "")),
                message=str(raw.get("message", "")),
                extras=extras,
            )
        except (ValueError, TypeError):
            # LogEntry's field validator rejects levels outside the
            # known set; map any future-looking level to the empty-
            # string → INFO fallback the validator already applies.
            entry = LogEntry(
                timestamp=str(raw.get("timestamp", "")),
                level="INFO",
                event=str(raw.get("event", "")),
                message=str(raw.get("message", "")),
                extras=extras,
            )
        out.append(entry)
    return out


@router.get("/mods/{request_id}/logs", response_model=ModLogsResponse, dependencies=[Depends(verify_api_key)])
async def get_mod_logs(
    request_id: str,
    limit: int = Query(
        100,
        ge=1,
        le=_MAX_LOG_LIMIT,
        description=(
            "Maximum number of log entries to return (newest-first). "
            "Clamped to the storage layer's per-request buffer cap "
            "so the response never silently truncates past the "
            "server-side retention."
        ),
    ),
) -> ModLogsResponse:
    """Return the captured status log entries for a request.

    Round v75 (Feature — status log monitoring). Companion to
    ``GET /v1/mods/{id}`` (current stage + progress) and
    ``GET /v1/mods/{id}/timeline`` (stage-by-stage timing). The
    three endpoints give an operator / Discord bot the full
    picture:

    - ``/v1/mods/{id}`` — "where is the request right now?"
    - ``/v1/mods/{id}/timeline`` — "how long did each stage take?"
    - ``/v1/mods/{id}/logs`` — "what actually happened?"

    The log stream is captured into Redis (``pipeline:logs:{id}``,
    a Redis LIST) by :func:`storage.redis.append_pipeline_log`.
    Hooking the orchestrator / pipeline nodes up to that helper
    is a follow-up round (v76+); this round ships the read-side
    infrastructure + endpoint so the wire shape is fixed before
    the writer side lands.

    Cache-first: reads from the Redis log list when available.
    A transient Redis error falls through to the DB existence
    check (logged at WARNING under ``api.logs.redis_error``).
    If the request exists in the DB but the Redis stream is
    missing/expired, the response is 200 with ``entries=[]``,
    ``source="db_unavailable"`` so the caller knows the request
    is real but the log stream has aged out. If the request
    exists in neither, the endpoint returns 404.

    Defensive against bad entries: the storage layer skips
    malformed JSON with a WARNING log, and this handler also
    skips non-dict entries and entries missing required
    fields (defensive defaults applied). The endpoint never
    raises on bad data — it always renders what it can.

    Why no auth? Mirrors the v52 ``/t2_judges`` rationale: the
    log entries are operational context, not sensitive payload.
    Each entry carries the request_id and any per-call context
    fields (phase, generator name, etc.) — none of which are
    secrets. Adding ``Depends(verify_api_key)`` is a one-line
    change if production needs it.
    """
    # Storage imports deferred to function body to keep
    # ``app.api.routes`` importable in unit-test environments
    # without Redis / Postgres (the conftest isolates env vars
    # but does not stub the modules themselves).
    from storage.redis import get_pipeline_logs as redis_get_logs
    from storage.redis import _PIPELINE_LOG_MAX_ENTRIES

    raw_entries: list[dict] = []
    redis_hit: bool = False
    try:
        # Clamp at the storage layer too — defense in depth in
        # case a future caller hits the function directly with
        # an out-of-range limit.
        raw_entries = await redis_get_logs(
            request_id, limit=min(limit, _PIPELINE_LOG_MAX_ENTRIES)
        )
        # A successful call that returned a non-empty list means
        # the Redis key exists and has data. The storage layer
        # collapses "key missing" and "key exists with 0 entries"
        # into the same empty-list return, so we can't tell the
        # two apart from the storage API alone; treat empty as
        # "key missing" so the DB fallback can confirm existence.
        redis_hit = len(raw_entries) > 0
    except (ConnectionError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "api.logs.redis_error",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    if redis_hit:
        entries = _build_log_entries(raw_entries)
        logger.info(
            "api.logs.cache_hit",
            request_id=request_id,
            count=len(entries),
            limit=limit,
        )
        return ModLogsResponse(
            request_id=request_id,
            entries=entries,
            count=len(entries),
            limit=limit,
            source="redis",
        )

    # Redis miss (key missing) OR transient Redis error: confirm
    # the request actually exists in the DB before claiming
    # "log unavailable" or returning 404.
    db_row = await get_mod_output(request_id)
    if not db_row:
        logger.warning("api.logs.not_found", request_id=request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found",
        )

    logger.info(
        "api.logs.db_only",
        request_id=request_id,
        limit=limit,
    )
    return ModLogsResponse(
        request_id=request_id,
        entries=[],
        count=0,
        limit=limit,
        source="db_unavailable",
    )