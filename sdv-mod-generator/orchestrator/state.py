"""Pipeline state dataclass passed through all LangGraph nodes."""
from dataclasses import dataclass, field
from typing import Literal

from generators.base import GeneratorOutput


@dataclass
class PipelineState:
    """Single source of truth through all pipeline nodes."""
    request_id: str
    user_id: str
    prompt: str
    phase: str = ""
    generators: list[str] = field(default_factory=list)
    hint: dict = field(default_factory=dict)
    outputs: dict[str, GeneratorOutput] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    zip_key: str | None = None
    t1_passed: bool = True
    t2_passed: bool = True
    t2_score: int = 0
    t2_feedback: str = ""
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
