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

logger = structlog.get_logger()


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
    logger.info("pipeline.generating", request_id=state.request_id, game=state.game, generators=state.generators)
    state.status = "generating"

    pack = get_game_pack(state.game)
    if pack is None:
        logger.error("pipeline.unknown_game", request_id=state.request_id, game=state.game)
        state.errors.append(f"Unknown game: {state.game}")
        state.status = "failed"
        return state

    for gen_name in state.generators:
        gen_cls = pack.get_generator(gen_name, state.phase)
        if gen_cls is None:
            logger.error("pipeline.generator_not_found", request_id=state.request_id, generator=gen_name, game=state.game)
            state.errors.append(f"Generator not found: {gen_name}")
            state.status = "failed"
            return state

        try:
            inp: GeneratorInput = {
                "prompt": state.prompt,
                "hint": state.hint,
                "request_id": state.request_id,
                "game": state.game,
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
    """Run Tier 2 LLM judge (advisory only — never blocks)."""
    logger.info("pipeline.t2_gate", request_id=state.request_id)
    state.status = "t2_gating"

    result = await run_t2(state.request_id, state.outputs)
    state.t2_passed = result.passed
    state.t2_available = result.available
    state.t2_score = result.score
    state.t2_feedback = result.feedback

    logger.info(
        "pipeline.t2_gate.done",
        request_id=state.request_id,
        score=result.score,
        passed=result.passed,
        feedback=result.feedback,
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

    builder.add_edge("t2_gate", "package")
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