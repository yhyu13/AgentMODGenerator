"""LangGraph pipeline — game-agnostic, pack-based."""
import asyncio
import structlog
from langgraph.graph import END, StateGraph

from generators.core import GeneratorInput
from generators.packager import package as packager_func
from orchestrator.state import PipelineState
from orchestrator.router import route
from quality.gate_t1 import run_t1
from quality.gate_t2 import run_t2
from generators.core import get_game_pack
from orchestrator.feedback_router import FeedbackRouter
from orchestrator._log_hook import emit_pipeline_log, emit_pipeline_log_async
from storage.redis import set_status as redis_set_status

logger = structlog.get_logger()
_feedback_router = FeedbackRouter()

#: Registry of in-flight background pipeline tasks, keyed by request_id.
#: ``cancel_mod`` (API) and the Discord ``/cancel`` command call
#: :func:`cancel_pipeline_task` to actually stop generation — before this
#: registry existed, cancellation only wrote a status key and the pipeline
#: kept running to completion (consuming LLM quota and eventually DM-ing a
#: "done" notification for a request the user cancelled).
_background_tasks: dict[str, asyncio.Task] = {}


def node_route(state: PipelineState) -> PipelineState:
    """Route prompt to game pack and phase/generators."""
    # v76 — wire pipeline log capture (structlog + Redis append) for
    # the read-side ``GET /v1/mods/{id}/logs`` endpoint. The fire-
    # and-forget pattern keeps the sync node from blocking on Redis.
    emit_pipeline_log(state.request_id, "info", "pipeline.routing",
                      prompt=state.prompt)
    try:
        _, hint = route(state.prompt)
        state.game = hint.get("game", "stardew_valley")
        state.phase = hint.get("phase", "shop_channel")
        state.generators = hint.get("generators", [])
        state.hint = hint
        if state.phase == "no_support":
            # Routed to the unsupported-request sentinel (e.g. a quest /
            # fish / monster prompt): fail fast with a clear error instead
            # of producing an unrelated mod. The conditional edge after
            # this node stops the graph here.
            state.status = "failed"
            concept = hint.get("matched_keyword", "")
            state.errors.append(
                f"unsupported_request: '{concept}' is not covered by any generator phase"
            )
            emit_pipeline_log(
                state.request_id, "error", "pipeline.unsupported_request",
                concept=concept,
            )
            return state
        state.status = "routing"
        emit_pipeline_log(
            state.request_id,
            "info",
            "pipeline.routing.done",
            game=state.game,
            phase=state.phase,
            generators=state.generators,
        )
    except Exception as exc:
        # v77 — extend pipeline log capture to error-state events so the
        # ``GET /v1/mods/{id}/logs`` endpoint surfaces failures, not only
        # the success-path transitions v76 wired.
        emit_pipeline_log(
            state.request_id, "error", "pipeline.routing.failed",
            error=str(exc),
        )
        state.errors.append(f"routing failed: {exc}")
        state.status = "failed"
        state.game = "stardew_valley"
        state.phase = "shop_channel"
        state.generators = []
        state.hint = {}
    return state


async def node_generate(state: PipelineState) -> PipelineState:
    """Run each generator in execution_order from the game pack."""
    # v77 — extend pipeline log capture to generation start event so the
    # ``GET /v1/mods/{id}/logs`` endpoint surfaces the iteration count
    # and generator list.
    emit_pipeline_log(
        state.request_id, "info", "pipeline.generating",
        game=state.game,
        generators=state.generators,
        t2_iterations=state.t2_iterations,
    )
    state.status = "generating"

    pack = get_game_pack(state.game)
    if pack is None:
        # v77 — extend pipeline log capture to error-state events.
        emit_pipeline_log(
            state.request_id, "error", "pipeline.unknown_game",
            game=state.game,
        )
        state.errors.append(f"Unknown game: {state.game}")
        state.status = "failed"
        return state

    gen_feedback_map: dict[str, str] = {}
    if state.t2_feedback:
        gen_feedback_map = _feedback_router.route(state.t2_feedback, state.generators)

    for gen_name in state.generators:
        gen_cls = pack.get_generator(gen_name, state.phase)
        if gen_cls is None:
            # v77 — extend pipeline log capture to error-state events.
            emit_pipeline_log(
                state.request_id, "error", "pipeline.generator_not_found",
                generator=gen_name, game=state.game,
            )
            state.errors.append(f"Generator not found: {gen_name}")
            state.generators_failed.append(gen_name)
            continue

        try:
            prior = {k: v for k, v in state.outputs.items()}
            gen_specific_feedback = gen_feedback_map.get(gen_name, "")
            inp: GeneratorInput = {
                "prompt": state.prompt,
                "hint": state.hint,
                "request_id": state.request_id,
                "game": state.game,
                "prior_outputs": prior,
                "t2_feedback": gen_specific_feedback,
            }
            gen = gen_cls()
            output = await gen.generate(inp)
            state.outputs[gen_name] = output
            state.generators_succeeded.append(gen_name)
            logger.info(
                "pipeline.generator_done",
                request_id=state.request_id,
                generator=gen_name,
                files=len(output.files),
            )
        except Exception as exc:
            # v77 — extend pipeline log capture to per-generator failure
            # events so operators can see WHICH generator blew up via the
            # ``GET /v1/mods/{id}/logs`` endpoint.
            emit_pipeline_log(
                state.request_id, "error", "pipeline.generator_failed",
                generator=gen_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            state.errors.append(f"{gen_name}: {type(exc).__name__}: {exc}")
            state.generators_failed.append(gen_name)
            # Continue with the next generator instead of fail-stop. The
            # final state.generators_failed list is surfaced in the API
            # response and the e2e regression test. This replaces the old
            # fail-stop behavior where one bad generator killed the whole
            # pipeline (the "swallowed errors" pattern from AGENTS.md
            # root-causes table).
            continue

    if state.generators_failed:
        logger.warning(
            "pipeline.partial_generation",
            request_id=state.request_id,
            failed=state.generators_failed,
            succeeded=state.generators_succeeded,
        )

    if not state.outputs and state.errors:
        logger.error("pipeline.no_outputs", request_id=state.request_id, errors=state.errors)
        state.errors.append("all generators failed: no outputs produced")
        state.status = "failed"

    return state


def node_t1_gate(state: PipelineState) -> PipelineState:
    """Run Tier 1 deterministic checks."""
    # v76 — wire pipeline log capture (structlog + Redis append).
    emit_pipeline_log(state.request_id, "info", "pipeline.t1_gate")
    state.status = "t1_gating"

    result = run_t1(state.request_id, state.outputs)
    state.t1_passed = result.passed

    if not result.passed:
        # v77 — extend pipeline log capture to T1 gate outcomes.
        emit_pipeline_log(
            state.request_id, "warning", "pipeline.t1_gate.failed",
            errors=result.errors,
        )
        state.errors.extend(result.errors)
        state.status = "failed"
    else:
        emit_pipeline_log(
            state.request_id, "info", "pipeline.t1_gate.passed",
        )

    return state


async def node_t2_gate(state: PipelineState) -> PipelineState:
    """Run Tier 2 LLM judge — blocks on failure up to max_t2_iterations."""
    state.t2_iterations += 1
    # v76 — wire pipeline log capture (structlog + Redis append).
    emit_pipeline_log(
        state.request_id, "info", "pipeline.t2_gate",
        iteration=state.t2_iterations,
    )
    state.status = "t2_gating"

    try:
        result = await run_t2(state.request_id, state.outputs)
        state.t2_passed = result.passed
        state.t2_available = result.available
        state.t2_score = result.score
        state.t2_feedback = result.feedback
        state.t2_panel_passed_count = sum(1 for jr in result.panel_results if jr.passed)
        state.t2_judge_results.append({
            "iteration": state.t2_iterations,
            "score": result.score,
            "feedback": result.feedback,
            "passed": result.passed,
            "panel_scores": [jr.score for jr in result.panel_results],
            "panel_passed_count": state.t2_panel_passed_count,
        })
    except Exception as exc:
        # v77 — extend pipeline log capture to T2 gate errors.
        emit_pipeline_log(
            state.request_id, "warning", "pipeline.t2_gate.error",
            error=str(exc),
        )
        state.t2_passed = True
        state.t2_available = False
        state.t2_score = 0
        state.t2_feedback = f"[T2 judge unavailable: {exc}]"

    # v77 — extend pipeline log capture to T2 gate completion event.
    emit_pipeline_log(
        state.request_id, "info", "pipeline.t2_gate.done",
        iteration=state.t2_iterations,
        score=state.t2_score,
        passed=state.t2_passed,
        feedback=state.t2_feedback,
    )
    return state


async def node_package(state: PipelineState) -> PipelineState:
    """Package outputs into zip."""
    # v76 — wire pipeline log capture (structlog + Redis append).
    emit_pipeline_log(state.request_id, "info", "pipeline.packaging")
    state.status = "packaging"

    all_files: dict[str, dict] = {}
    all_assets: list[str] = []
    for name, output in state.outputs.items():
        all_files.update(output.files)
        all_assets.extend(output.assets)

    from app.config import get_config

    timeout = get_config().zip_output_timeout
    try:
        zip_key = await asyncio.wait_for(
            asyncio.to_thread(packager_func, state.request_id, all_files, all_assets),
            timeout=timeout,
        )
        state.zip_key = zip_key
        state.status = "done"
        # v77 — extend pipeline log capture to packaging completion event.
        emit_pipeline_log(
            state.request_id, "info", "pipeline.done",
            zip_key=zip_key,
        )
    except asyncio.TimeoutError:
        # v77 — extend pipeline log capture to packaging error paths.
        emit_pipeline_log(
            state.request_id, "error", "pipeline.packaging_timeout",
            timeout=timeout,
        )
        state.errors.append(f"packaging timed out after {timeout} seconds")
        state.status = "failed"
    except Exception as exc:
        emit_pipeline_log(
            state.request_id, "error", "pipeline.packaging_failed",
            error=str(exc),
        )
        state.errors.append(f"packaging failed: {exc}")
        state.status = "failed"
    return state


def build_graph() -> StateGraph:
    """Build and return the LangGraph StateGraph."""
    builder = StateGraph(PipelineState)

    builder.add_node("route", node_route)
    builder.add_node("generate", node_generate)
    builder.add_node("t1_gate", node_t1_gate)
    builder.add_node("t2_gate", node_t2_gate)
    builder.add_node("package", node_package)

    builder.set_entry_point("route")

    def route_should_continue(state: PipelineState) -> str:
        # Stop the graph immediately after routing on an unsupported-request
        # (or any routing) failure instead of running generate/t1 on an
        # empty generator list and stacking "no outputs" errors.
        if state.status == "failed":
            return "end"
        return "generate"

    builder.add_conditional_edges(
        "route",
        route_should_continue,
        {"end": END, "generate": "generate"},
    )
    builder.add_edge("generate", "t1_gate")

    def t1_should_continue(state: PipelineState) -> str:
        if state.status == "failed":
            return "end"
        return "t2_gate"

    builder.add_conditional_edges(
        "t1_gate",
        t1_should_continue,
        {
            "end": END,
            "t2_gate": "t2_gate",
        },
    )

    def t2_should_continue(state: PipelineState) -> str:
        """After T2 gate: loop back to generate if T2 failed and iterations remain."""
        if state.status == "failed":
            return "end"
        if state.t2_passed:
            return "package"
        # t2_iterations is incremented in node_t2_gate (state-mutation in this
        # function is dropped by LangGraph between nodes — see AGENTS.md root
        # cause "max_t2_iterations=2 + invalid LLM output = infinite loop").
        if state.t2_iterations < state.max_t2_iterations:
            logger.info(
                "pipeline.t2.retry",
                request_id=state.request_id,
                iteration=state.t2_iterations,
                max=state.max_t2_iterations,
                feedback=state.t2_feedback[:100],
            )
            return "generate"
        # Max iterations reached — ship it (advisory fallback)
        logger.warning(
            "pipeline.t2.max_iterations",
            request_id=state.request_id,
            iterations=state.t2_iterations,
        )
        return "package"

    builder.add_conditional_edges(
        "t2_gate",
        t2_should_continue,
        {
            "generate": "generate",  # loop back with incremented counter
            "package": "package",
            "end": END,
        },
    )

    builder.add_edge("package", END)

    return builder.compile()


_graph = None


def get_graph() -> StateGraph:
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_pipeline(request_id: str, user_id: str, prompt: str) -> PipelineState:
    """Run the full pipeline."""
    graph = get_graph()
    # v109 — wire ``Config.max_t2_iterations`` (parsed from
    # ``MAX_T2_ITERATIONS``) into the constructed ``PipelineState``.
    # Pre-v109 the field defaulted to ``0`` and was never set
    # anywhere in production, so the T2 retry loop guard
    # (``if state.t2_iterations < state.max_t2_iterations``) was
    # always False on the first iteration — T2 ran once and shipped.
    # Operators can now opt in to T2 retries via
    # ``MAX_T2_ITERATIONS=N`` (1 or 2) in their env. Imported
    # lazily inside the function to preserve the existing
    # lazy-import convention used by every other ``app.*`` dep
    # in this module (``get_config`` is already pulled in lazily
    # by ``node_package`` at line 241).
    from app.config import get_config

    initial_state = PipelineState(
        request_id=request_id,
        user_id=user_id,
        prompt=prompt,
        max_t2_iterations=get_config().max_t2_iterations,
    )
    # v76 — wire pipeline log capture (structlog + Redis append).
    # Use the async variant so the log stream is flushed before
    # ``run_pipeline`` returns — callers that await this coroutine
    # get a guarantee that the start event is in the Redis list.
    await emit_pipeline_log_async(
        request_id, "info", "pipeline.start",
        user_id=user_id, prompt=prompt,
    )
    result_dict = await graph.ainvoke(initial_state)
    return PipelineState(**result_dict)


async def _run_pipeline_and_update_status(request_id: str, user_id: str, prompt: str) -> PipelineState:
    """Run pipeline and update Redis + PostgreSQL status on completion."""
    from app.metrics import (
        record_generator_outcome,
        record_pipeline_run,
        record_t2_score,
    )
    from storage.queries import save_mod_output, update_mod_request_status
    from storage.redis import set_pipeline_state

    try:
        result = await run_pipeline(request_id, user_id, prompt)
    except asyncio.CancelledError:
        # Real cancellation: the request_id → Task registry called
        # task.cancel(). Persist the cancelled disposition so the status
        # endpoint and notifier see it (a cancelled request must NOT be
        # DM'd as "done" later).
        await redis_set_status(request_id, "cancelled")
        await update_mod_request_status(request_id, "cancelled")
        emit_pipeline_log(
            request_id, "info", "pipeline.cancelled",
            user_id=user_id,
        )
        raise

    await redis_set_status(request_id, result.status)
    if result.zip_key:
        await redis_set_status(request_id, f"done:{result.zip_key}")
    elif result.status == "failed":
        await redis_set_status(request_id, "failed")

    await update_mod_request_status(request_id, result.status)
    await set_pipeline_state(request_id, {
        "status": result.status,
        "errors": result.errors,
        "generators_failed": result.generators_failed,
        "generators_succeeded": result.generators_succeeded,
        "outputs": {
            name: {"files": out.files, "assets": out.assets, "metadata": out.metadata}
            for name, out in (result.outputs or {}).items()
        },
        "t2_feedback": result.t2_feedback,
        "t2_score": result.t2_score,
        "t2_passed": result.t2_passed,
        "t2_available": result.t2_available,
        "t2_max_score": 10,
        "t2_pass_threshold": 7,
        "t2_panel_passed_count": result.t2_panel_passed_count,
        "zip_key": result.zip_key,
    })

    files_preview = [path for out in result.outputs.values() for path in out.files.keys()]
    await save_mod_output(
        request_id=request_id,
        zip_key=result.zip_key,
        zip_url=None,
        files_preview=files_preview,
        t1_errors=result.errors,
        t2_feedback=result.t2_feedback,
        t2_score=result.t2_score,
    )

    # Metrics — keep label cardinality bounded (no per-request-id).
    record_pipeline_run(status=result.status)
    if result.t2_available and 0 <= result.t2_score <= 10:
        record_t2_score(result.t2_score)
    for gen_name in result.generators_succeeded:
        record_generator_outcome(gen_name, succeeded=True)
    for gen_name in result.generators_failed:
        record_generator_outcome(gen_name, succeeded=False)

    # v77 — extend pipeline log capture to the post-pipeline status update
    # so the ``GET /v1/mods/{id}/logs`` endpoint shows the final disposition
    # alongside every transition.
    emit_pipeline_log(
        request_id, "info", "pipeline.status_updated",
        status=result.status,
        zip_key=result.zip_key,
        t2_score=result.t2_score,
    )
    return result


def run_pipeline_background(request_id: str, user_id: str, prompt: str) -> asyncio.Task:
    """Run pipeline in background using asyncio.create_task.

    Registers the task in the ``request_id → Task`` registry so
    :func:`cancel_pipeline_task` can actually stop it. The registry entry
    is removed on completion (success, failure, or cancellation).
    """
    task = asyncio.create_task(_run_pipeline_and_update_status(request_id, user_id, prompt))
    _background_tasks[request_id] = task

    def _forget(finished: asyncio.Task) -> None:
        if _background_tasks.get(request_id) is finished:
            _background_tasks.pop(request_id, None)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error(
                "pipeline.background_task_error",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    task.add_done_callback(_forget)
    return task


def cancel_pipeline_task(request_id: str) -> bool:
    """Actually cancel an in-flight background pipeline task.

    Returns ``True`` if a task was found and cancelled, ``False`` if no
    task is registered (already finished, never started, or cancelled).
    The pipeline coroutine catches ``asyncio.CancelledError`` and persists
    the ``cancelled`` disposition before the task completes.
    """
    task = _background_tasks.get(request_id)
    if task is None or task.done():
        return False
    task.cancel()
    logger.info(
        "pipeline.cancel_requested",
        request_id=request_id,
    )
    return True


async def _run_pipeline_sync(request_id: str, user_id: str, prompt: str) -> None:
    """Async wrapper for use with asyncio.create_task — runs pipeline in background."""
    # v77 — extend pipeline log capture to background-task lifecycle events.
    emit_pipeline_log(
        request_id, "info", "pipeline.background_started",
    )
    try:
        await _run_pipeline_and_update_status(request_id, user_id, prompt)
    except Exception as e:
        emit_pipeline_log(
            request_id, "error", "pipeline.background_error",
            error=str(e),
        )