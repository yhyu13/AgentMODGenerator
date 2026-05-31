"""LangGraph pipeline — real implementation."""
import structlog
from langgraph.graph import END, StateGraph

from generators.base import GeneratorInput
from generators.packager import package as packager_func
from orchestrator.state import PipelineState
from orchestrator.router import route
from quality.gate_t1 import run_t1
from quality.gate_t2 import run_t2
from generators.registry import get as get_generator

logger = structlog.get_logger()


def node_route(state: PipelineState) -> PipelineState:
    """Route prompt to generators."""
    logger.info("pipeline.routing", request_id=state.request_id, prompt=state.prompt)
    phase, hint = route(state.prompt)
    state.phase = phase
    state.generators = hint["generators"]
    state.hint = hint
    state.status = "routing"
    logger.info(
        "pipeline.routing.done",
        request_id=state.request_id,
        phase=phase,
        generators=state.generators,
    )
    return state


def node_generate(state: PipelineState) -> PipelineState:
    """Run each generator in execution_order from hint."""
    logger.info("pipeline.generating", request_id=state.request_id, generators=state.generators)
    state.status = "generating"

    # Use execution_order from hint if present, otherwise fall back to generators list
    execution_order: list[str] = state.hint.get("execution_order", state.generators)

    for gen_name in execution_order:
        gen_cls = get_generator(gen_name)
        if gen_cls is None:
            logger.warning("pipeline.generator_not_found", request_id=state.request_id, generator=gen_name)
            state.errors.append(f"Generator not found: {gen_name}")
            continue

        try:
            inp: GeneratorInput = {
                "prompt": state.prompt,
                "hint": state.hint,
                "request_id": state.request_id,
            }
            gen = gen_cls()
            output = gen.generate(inp)
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


def node_t2_gate(state: PipelineState) -> PipelineState:
    """Run Tier 2 LLM judge (advisory only — never blocks)."""
    logger.info("pipeline.t2_gate", request_id=state.request_id)
    state.status = "t2_gating"

    result = run_t2(state.request_id, state.outputs)
    state.t2_passed = result.passed
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


def node_package(state: PipelineState) -> PipelineState:
    """Package outputs into zip."""
    logger.info("pipeline.packaging", request_id=state.request_id)
    state.status = "packaging"

    all_files: dict[str, dict] = {}
    all_assets: list[str] = []
    for name, output in state.outputs.items():
        all_files.update(output.files)
        all_assets.extend(output.assets)

    zip_key = packager_func(state.request_id, all_files, all_assets)
    state.zip_key = zip_key
    state.status = "done"
    logger.info("pipeline.done", request_id=state.request_id, zip_key=zip_key)
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

    # Conditional: if T1 failed, stop here; otherwise continue to T2
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


# Singleton graph instance
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
    # LangGraph returns a dict; reconstruct PipelineState for type safety
    return PipelineState(**result_dict)
