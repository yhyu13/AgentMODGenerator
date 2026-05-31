"""Tier 2 LLM semantic judge — stub."""
from dataclasses import dataclass, field

import structlog
from generators.base import GeneratorOutput

logger = structlog.get_logger()


@dataclass
class T2Result:
    passed: bool
    score: int  # 1-10
    feedback: str = ""


def run_t2(request_id: str, outputs: dict[str, GeneratorOutput]) -> T2Result:
    """Run Tier 2 LLM judge (stub: always passes with score=10).

    In real impl, checks:
    - Does the mod make sense semantically?
    - Do item descriptions match their prices?
    - Do mail triggers reference valid events?
    - Is the overall mod coherent and not obviously exploitative?
    """
    logger.info("quality.t2.run", request_id=request_id)
    result = T2Result(passed=True, score=10, feedback="[STUB] Score 10 — L2 judge not implemented")
    logger.info("quality.t2.done", request_id=request_id, score=10)
    return result
