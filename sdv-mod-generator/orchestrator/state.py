"""Pipeline state dataclass passed through all LangGraph nodes."""
from dataclasses import dataclass, field
from typing import Literal

from generators.core import GeneratorOutput


@dataclass
class PipelineState:
    """Single source of truth through all pipeline nodes."""
    request_id: str
    user_id: str
    prompt: str
    game: str = "stardew_valley"
    phase: str = ""
    generators: list[str] = field(default_factory=list)
    hint: dict = field(default_factory=dict)
    outputs: dict[str, GeneratorOutput] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    generators_failed: list[str] = field(default_factory=list)
    generators_succeeded: list[str] = field(default_factory=list)
    zip_key: str | None = None
    t1_passed: bool = True
    t2_passed: bool = True
    t2_available: bool = True
    t2_score: int = 0
    t2_feedback: str = ""
    t2_iterations: int = 0
    # v109 — T2 retry loop upper bound. Pre-v109 this field was
    # always the dataclass default of ``0`` and never set in
    # production (no caller constructed ``PipelineState`` with an
    # explicit value), making the pipeline conditional
    # ``if state.t2_iterations < state.max_t2_iterations``
    # always False on the first iteration — T2 ran once and
    # shipped. v109 wires this field from
    # ``Config.max_t2_iterations`` (parsed from ``MAX_T2_ITERATIONS``)
    # in ``orchestrator.pipeline.run_pipeline``. Default ``0`` here
    # keeps existing unit tests green (none of them set the field
    # explicitly and they assert T2 ships immediately on first
    # iteration); production reads the singleton at request time.
    max_t2_iterations: int = 0
    t2_judge_results: list = field(default_factory=list)
    t2_panel_passed_count: int = 0  # number of judges with score >= threshold
    status: Literal[
        "pending",
        "routing",
        "generating",
        "t1_gating",
        "t2_gating",
        "packaging",
        "done",
        "failed",
    ] = "pending"