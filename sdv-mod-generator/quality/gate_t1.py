"""Tier 1 deterministic quality checks — stub."""
from dataclasses import dataclass, field

import structlog
from generators.base import GeneratorOutput

logger = structlog.get_logger()


@dataclass
class T1Result:
    passed: bool
    errors: list[str] = field(default_factory=list)


def run_t1(request_id: str, outputs: dict[str, GeneratorOutput]) -> T1Result:
    """Run Tier 1 deterministic checks (stub: always pass).

    In real impl, checks:
    - Valid JSON (manifest.json, content.json)
    - Required fields present
    - Field values in valid range (price >= 0, sprite IDs exist)
    - Schema compliance for Content Patcher actions
    - No broken internal references
    - i18n keys referenced exist in i18n files
    """
    logger.info("quality.t1.run", request_id=request_id)
    result = T1Result(passed=True, errors=[])
    logger.info("quality.t1.done", request_id=request_id, passed=True)
    return result
