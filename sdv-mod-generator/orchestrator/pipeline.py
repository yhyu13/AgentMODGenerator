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
from storage.redis import set_status as redis_set_status

logger = structlog.get_logger()
_feedback_router = FeedbackRouter()


def node_route(state: PipelineState) -> PipelineState:
    """Route prompt to game pack and phase/generators."""
    logger.info("pipeline.routing", request_id=state.request_id, prompt=state.prompt)
    try:
        _, hint = route(state.prompt)
        state.game = hint.get("game", "stardew_valley")
        state.phase = hint.get("phase", "shop_channel")
        state.generators = hint.get("generators", [])
        state.hint = hint
        state.status = "routing"
        logger.info(
            "pipeline.routing.done",
            request_id=state.request_id,
            game=state.game,
            phase=state.phase,
            generators=state.generators,
        )
    except Exception as exc:
        logger.error("pipeline.routing.failed", request_id=state.request_id, error=str(exc))
        state.errors.append(f"routing failed: {exc}")
        state.status = "failed"
        state.game = "stardew_valley"
        state.phase = "shop_channel"
        state.generators = []
        state.hint = {}
    return state


async def node_generate(state: PipelineState) -> PipelineState:
    """Run each generator in execution_order from the game pack."""
    logger.info(
        "pipeline.generating",
        request_id=state.request_id,
        game=state.game,
        generators=state.generators,
        t2_iterations=state.t2_iterations,
    )
    state.status = "generating"

    pack = get_game_pack(state.game)
    if pack is None:
        logger.error("pipeline.unknown_game", request_id=state.request_id, game=state.game)
        state.errors.append(f"Unknown game: {state.game}")
        state.status = "failed"
        return state

    gen_feedback_map: dict[str, str] = {}
    if state.t2_feedback:
        gen_feedback_map = _feedback_router.route(state.t2_feedback, state.generators)

    for gen_name in state.generators:
        gen_cls = pack.get_generator(gen_name, state.phase)
        if gen_cls is None:
            logger.error("pipeline.generator_not_found", request_id=state.request_id, generator=gen_name, game=state.game)
            state.errors.append(f"Generator not found: {gen_name}")
            state.status = "failed"
            return state

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
            logger.info(
                "pipeline.generator_done",
                request_id=state.request_id,
                generator=gen_name,
                files=len(output.files),
            )
        except Exception as exc:
            logger.error("pipeline.generator_failed", request_id=state.request_id, generator=gen_name, error=str(exc))
            state.errors.append(f"{gen_name}: {exc}")
            state.status = "failed"
            return state

    if not state.outputs and state.errors:
        logger.error("pipeline.no_outputs", request_id=state.request_id, errors=state.errors)
        state.errors.append("all generators failed: no outputs produced")

    return state


def node_t1_gate(state: PipelineState) -> PipelineState:
    """Run Tier 1 deterministic checks."""
    logger.info("pipeline.t1_gate", request_id=state.request_id)
    state.status = "t1_gating"

    result = run_t1(state.request_id, state.outputs)
    state.t1_passed = result.passed

    if not result.passed:
        logger.warning("pipeline.t1_gate.failed", request_id=state.request_id, errors=result.errors)
        state.errors.extend(result.errors)
        state.status = "failed"
    else:
        logger.info("pipeline.t1_gate.passed", request_id=state.request_id)

    return state


async def node_t2_gate(state: PipelineState) -> PipelineState:
    """Run Tier 2 LLM judge — blocks on failure up to max_t2_iterations."""
    logger.info("pipeline.t2_gate", request_id=state.request_id, iteration=state.t2_iterations)
    state.status = "t2_gating"

    try:
        result = await run_t2(state.request_id, state.outputs)
        state.t2_passed = result.passed
        state.t2_available = result.available
        state.t2_score = result.score
        state.t2_feedback = result.feedback
        state.t2_judge_results.append({
            "iteration": state.t2_iterations,
            "score": result.score,
            "feedback": result.feedback,
            "passed": result.passed,
        })
    except Exception as exc:
        logger.warning("pipeline.t2_gate.error", request_id=state.request_id, error=str(exc))
        state.t2_passed = True
        state.t2_available = False
        state.t2_score = 0
        state.t2_feedback = f"[T2 judge unavailable: {exc}]"

    logger.info(
        "pipeline.t2_gate.done",
        request_id=state.request_id,
        iteration=state.t2_iterations,
        score=state.t2_score,
        passed=state.t2_passed,
        feedback=state.t2_feedback,
    )
    return state


async def node_package(state: PipelineState) -> PipelineState:
    """Package outputs into zip."""
    logger.info("pipeline.packaging", request_id=state.request_id)
    state.status = "packaging"

    all_files: dict[str, dict] = {}
    all_assets: list[str] = []
    for name, output in state.outputs.items():
        all_files.update(output.files)
        all_assets.extend(output.assets)

    try:
        zip_key = await asyncio.wait_for(
            asyncio.to_thread(packager_func, state.request_id, all_files, all_assets),
            timeout=300,
        )
        state.zip_key = zip_key
        state.status = "done"
        logger.info("pipeline.done", request_id=state.request_id, zip_key=zip_key)
    except asyncio.TimeoutError:
        logger.error("pipeline.packaging_timeout", request_id=state.request_id)
        state.errors.append("packaging timed out after 300 seconds")
        state.status = "failed"
    except Exception as exc:
        logger.error("pipeline.packaging_failed", request_id=state.request_id, error=str(exc))
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
    builder.add_edge("route", "generate")
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
        # T2 failed — check if we have iterations left
        if state.t2_iterations < state.max_t2_iterations:
            state.t2_iterations += 1
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
    initial_state = PipelineState(
        request_id=request_id,
        user_id=user_id,
        prompt=prompt,
    )
    logger.info("pipeline.start", request_id=request_id, user_id=user_id, prompt=prompt)
    result_dict = await graph.ainvoke(initial_state)
    return PipelineState(**result_dict)


async def _run_pipeline_and_update_status(request_id: str, user_id: str, prompt: str) -> PipelineState:
    """Run pipeline and update Redis status with zip_key and t2_score on completion."""
    result = await run_pipeline(request_id, user_id, prompt)
    await redis_set_status(request_id, result.status)
    if result.zip_key:
        await redis_set_status(request_id, f"done:{result.zip_key}")
    elif result.status == "failed":
        await redis_set_status(request_id, "failed")
    logger.info(
        "pipeline.status_updated",
        request_id=request_id,
        status=result.status,
        zip_key=result.zip_key,
        t2_score=result.t2_score,
    )
    return result


def run_pipeline_background(request_id: str, user_id: str, prompt: str) -> asyncio.Task:
    """Run pipeline in background using asyncio.create_task."""
    return asyncio.create_task(_run_pipeline_and_update_status(request_id, user_id, prompt))