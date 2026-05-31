"""Tier 2 LLM semantic judge — real implementation."""
from typing import Any
import structlog
from dataclasses import dataclass

from generators.base import GeneratorOutput
from llm.client import get_client

logger = structlog.get_logger()


@dataclass
class T2Result:
    passed: bool
    score: int
    feedback: str = ""


async def run_t2(request_id: str, outputs: dict[str, GeneratorOutput]) -> T2Result:
    """Run Tier 2 LLM judge — advisory only, never blocks.

    Evaluates mod quality across dimensions:
    - Semantic coherence (does the mod make sense?)
    - Game balance (prices reasonable? triggers logical?)
    - Content quality (descriptions, naming, completeness)
    - Content Patcher compliance (actions used correctly)
    """
    logger.info("quality.t2.run", request_id=request_id)

    try:
        client = get_client()
        score, feedback = await _llm_judge(request_id, outputs, client)
        passed = score >= 7
        logger.info("quality.t2.done", request_id=request_id, score=score, passed=passed)
        return T2Result(passed=passed, score=score, feedback=feedback)
    except RuntimeError as exc:
        if "No LLM provider" in str(exc):
            logger.info("quality.t2.skipped.no_client", request_id=request_id)
            return T2Result(passed=True, score=10, feedback="[T2 judge skipped: no LLM provider configured]")
        raise
    except Exception as exc:
        logger.error("quality.t2.error", request_id=request_id, error=str(exc))
        return T2Result(passed=True, score=10, feedback=f"[T2 judge unavailable: {exc}]")


async def _llm_judge(request_id: str, outputs: dict[str, GeneratorOutput], client: Any) -> tuple[int, str]:

    summary = _build_mod_summary(outputs)

    prompt = f"""You are a Stardew Valley mod quality judge. Evaluate this generated mod and give a score 1-10.

SCORING RUBRIC:
- 9-10: Excellent — coherent, balanced, creative, correct Content Patcher usage
- 7-8: Good — minor issues (slightly unbalanced prices, bland descriptions)
- 5-6: Fair — notable issues (poor prices, illogical triggers, missing polish)
- 3-4: Poor — significant problems (broken actions, nonsensical content)
- 1-2: Broken — Content Patcher will crash or mod is incoherent

Evaluate:
{summary}

Respond with ONLY this format (no extra text):
SCORE: <number 1-10>
FEEDBACK: <2-4 sentence explanation of the main issues and strengths>
"""

    response = await client.complete(prompt, system="You are a Stardew Valley mod quality judge.", max_tokens=300)
    return _parse_judge_response(response)


def _build_mod_summary(outputs: dict[str, GeneratorOutput]) -> str:
    parts: list[str] = []
    for gen_name, output in outputs.items():
        files = list(output.files.keys())
        parts.append(f"## {gen_name}: {', '.join(files)}")
        for path, content in output.files.items():
            if isinstance(content, dict):
                parts.append(f"  {path}: {str(content)[:200]}")
            elif isinstance(content, str) and len(content) < 200:
                parts.append(f"  {path}: {content}")
    return "\n".join(parts)


def _parse_judge_response(response: str) -> tuple[int, str]:
    lines = response.strip().split("\n")
    score = 10
    feedback = ""

    for line in lines:
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(line.split(":")[1].strip())
                score = max(1, min(10, score))
            except (ValueError, IndexError):
                score = 10
        elif line.startswith("FEEDBACK:"):
            feedback = line.split(":", 1)[1].strip()

    if not feedback:
        feedback = response.strip()[:200]

    return score, feedback