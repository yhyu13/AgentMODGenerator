"""Tier 2 LLM judge panel — 3 diverse agents evaluating mod quality."""
import asyncio
import re
from typing import Any
import structlog
from dataclasses import dataclass, field

from generators.base import GeneratorOutput
from llm.client import get_client

logger = structlog.get_logger()

_INJECTION_PATTERNS = [
    re.compile(r"^SCORE:\s*\d+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^FEEDBACK:\s*.+", re.MULTILINE | re.IGNORECASE),
]

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_SCORE_FALLBACK_RE = re.compile(r"\b(10|[0-9])\b")
_VERDICT_LINE_RE = re.compile(r"^(SCORE|FEEDBACK)\s*:", re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models.

    Some models (e.g. MiniMax-M2.7, Claude with extended thinking) prepend a thinking
    block before the structured answer. If we don't strip it, the parser sees
    'SCORE:' only as raw text inside the think block and the verdict never lands
    on its own line, so score defaults to 0 and feedback is the truncated think
    block — i.e. the false-pass from commit 431c3fe.
    """
    if not text:
        return ""
    stripped = _THINK_BLOCK_RE.sub("", text)
    return stripped.strip()


def _sanitize_for_judge(content: str) -> str:
    sanitized = content
    for pattern in _INJECTION_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


@dataclass
class JudgeResult:
    judge_name: str
    score: int
    feedback: str
    passed: bool


@dataclass
class T2Result:
    available: bool
    passed: bool
    score: int
    feedback: str = ""
    panel_results: list[JudgeResult] = field(default_factory=list)


JUDGE_PERSONAS = {
    "game_balance": {
        "name": "GameBalanceJudge",
        "system": "You are a Stardew Valley game balance specialist. You evaluate whether mod content maintains reasonable game economy, pricing, and mechanical balance.",
        "focus": "prices, item values, trigger frequencies, progression balance, economic impact",
    },
    "content_quality": {
        "name": "ContentQualityJudge",
        "system": "You are a Stardew Valley narrative and content quality specialist. You evaluate descriptions, naming, creativity, and flavor text.",
        "focus": "descriptions, naming, dialogue, flavor text, immersion, creativity",
    },
    "technical_compliance": {
        "name": "TechnicalComplianceJudge",
        "system": "You are a Stardew Valley Content Patcher technical specialist. You evaluate whether mod uses correct actions, proper JSON structure, and valid game paths.",
        "focus": "Content Patcher actions, JSON validity, file paths, action parameters, manifest",
    },
}


async def run_t2(request_id: str, outputs: dict[str, GeneratorOutput]) -> T2Result:
    """Run Tier 2 judge panel — 3 diverse agents evaluating mod quality in parallel."""
    logger.info("quality.t2.run", request_id=request_id)

    try:
        client = get_client()
        panel_results = await _run_judge_panel(request_id, outputs, client)

        if not panel_results:
            return T2Result(available=False, passed=True, score=0, feedback="[T2 judge panel failed: no results]")

        avg_score = sum(r.score for r in panel_results) // len(panel_results)
        passed_count = sum(1 for r in panel_results if r.passed)
        panel_passed = passed_count >= 2

        aggregate_feedback = _aggregate_feedback(panel_results)

        logger.info(
            "quality.t2.done",
            request_id=request_id,
            panel_scores=[r.score for r in panel_results],
            avg_score=avg_score,
            passed_count=passed_count,
            panel_passed=panel_passed,
        )
        return T2Result(
            available=True,
            passed=panel_passed,
            score=avg_score,
            feedback=aggregate_feedback,
            panel_results=panel_results,
        )
    except RuntimeError as exc:
        if "No LLM provider" in str(exc):
            logger.info("quality.t2.skipped.no_client", request_id=request_id)
            return T2Result(available=False, passed=True, score=0, feedback="[T2 judge skipped: no LLM provider configured]")
        raise
    except Exception as exc:
        logger.error("quality.t2.error", request_id=request_id, error=str(exc))
        return T2Result(available=False, passed=True, score=0, feedback=f"[T2 judge unavailable: {exc}]")


async def _run_judge_panel(request_id: str, outputs: dict[str, GeneratorOutput], client: Any) -> list[JudgeResult]:
    """Run all 3 judges in parallel and return their individual results."""
    summary = _build_mod_summary(outputs)

    tasks = [
        _llm_judge(request_id, summary, client, persona)
        for persona in JUDGE_PERSONAS.values()
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    panel: list[JudgeResult] = []
    for persona, result in zip(JUDGE_PERSONAS.values(), results):
        if isinstance(result, Exception):
            logger.warning("quality.t2.judge_error", judge=persona["name"], error=str(result))
            continue
        score, feedback, passed = result
        panel.append(JudgeResult(judge_name=persona["name"], score=score, feedback=feedback, passed=passed))

    return panel


async def _llm_judge(
    request_id: str,
    summary: str,
    client: Any,
    persona: dict[str, str],
) -> tuple[int, str, bool]:
    """Run a single judge with given persona, return (score, feedback, passed)."""
    prompt = f"""You are a {persona["name"]}.

YOUR FOCUS: {persona["focus"]}

Evaluate this Stardew Valley mod and give a score 1-10.

SCORING RUBRIC:
- 9-10: Excellent — {persona["focus"]} is outstanding
- 7-8: Good — minor issues in {persona["focus"]}
- 5-6: Fair — notable issues in {persona["focus"]}
- 3-4: Poor — significant problems in {persona["focus"]}
- 1-2: Broken — critical issues in {persona["focus"]}

Mod content to evaluate:
{summary}

Respond with ONLY this format (no extra text):
SCORE: <number 1-10>
FEEDBACK: <2-4 sentence explanation of the main issues and strengths>
"""

    response = await client.complete(prompt, system=persona["system"], max_tokens=1000)
    score, feedback = _parse_judge_response(response)
    passed = score >= 7
    return score, feedback, passed


def _build_mod_summary(outputs: dict[str, GeneratorOutput]) -> str:
    parts: list[str] = []
    for gen_name, output in outputs.items():
        files = list(output.files.keys())
        parts.append(f"## {gen_name}: {', '.join(files)}")
        for path, content in output.files.items():
            if isinstance(content, dict):
                content_str = str(content)
                sanitized = _sanitize_for_judge(content_str)
                if len(sanitized) > 200:
                    try:
                        import json
                        truncated = json.loads(sanitized)
                        truncated = {k: (v[:50] + "..." if isinstance(v, str) and len(v) > 50 else v) for k, v in truncated.items()}
                        sanitized = json.dumps(truncated)
                    except Exception:
                        sanitized = sanitized[:197] + "..."
                parts.append(f"  {path}: {sanitized}")
            elif isinstance(content, str):
                sanitized = _sanitize_for_judge(content)
                if len(sanitized) > 200:
                    sanitized = sanitized[:197] + "..."
                parts.append(f"  {path}: {sanitized}")
    return "\n".join(parts)


def _parse_judge_response(response: str) -> tuple[int, str]:
    """Parse a judge's response into (score, feedback).

    Handles three failure modes from commit 431c3fe:
    1. Reasoning model emits ``...`` block before the verdict — stripped first.
    2. Model returns verdict on a single line (no newline separator) — regex fallback.
    3. Model returns no SCORE: line at all — search for a lone 0-10 number as last resort.
    """
    stripped = _strip_think_blocks(response)
    lines = stripped.split("\n") if stripped else []
    score = 0
    feedback = ""
    found_verdict_line = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _VERDICT_LINE_RE.match(line):
            found_verdict_line = True
        if line.upper().startswith("SCORE:"):
            try:
                token = line.split(":", 1)[1].strip().split()[0]
                score = int(token)
                score = max(0, min(10, score))
            except (ValueError, IndexError):
                score = 0
        elif line.upper().startswith("FEEDBACK:"):
            feedback = line.split(":", 1)[1].strip()

    if not found_verdict_line:
        m = _SCORE_FALLBACK_RE.search(stripped)
        if m:
            try:
                score = max(0, min(10, int(m.group(1))))
            except ValueError:
                pass

    if not feedback:
        feedback = stripped[-200:] if stripped else response.strip()[:200]

    return score, feedback


def _aggregate_feedback(panel_results: list[JudgeResult]) -> str:
    """Combine individual judge feedbacks into a single aggregate feedback."""
    if not panel_results:
        return "[No judge feedback available]"

    parts = []
    for result in panel_results:
        parts.append(f"[{result.judge_name}] {result.feedback}")

    passed_count = sum(1 for r in panel_results if r.passed)
    parts.append(f"(Panel: {passed_count}/{len(panel_results)} judges passed)")

    return " ".join(parts)