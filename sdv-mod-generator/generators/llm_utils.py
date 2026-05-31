"""LLM generation utilities for generators."""
import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_KB_DIR = Path(__file__).parent.parent / "knowledge" / "data"

_item_ids: dict[str, Any] | None = None
_game_systems: dict[str, Any] | None = None
_content_actions: dict[str, Any] | None = None


def _load_kb_json(name: str) -> dict[str, Any]:
    path = _KB_DIR / f"{name}.json"
    if not path.exists():
        logger.warning("llm_utils.kb_missing", name=name)
        return {}
    with open(path) as f:
        return json.load(f)


def get_item_ids() -> dict[str, Any]:
    global _item_ids
    if _item_ids is None:
        _item_ids = _load_kb_json("item_ids")
    return _item_ids


def get_game_systems() -> dict[str, Any]:
    global _game_systems
    if _game_systems is None:
        _game_systems = _load_kb_json("game_systems")
    return _game_systems


def get_content_actions() -> dict[str, Any]:
    global _content_actions
    if _content_actions is None:
        _content_actions = _load_kb_json("content_actions")
    return _content_actions


async def generate_structured(
    prompt: str,
    output_schema: type,
    system: str | None = None,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Generate structured JSON output via LLM.

    Uses OpenAI if ANTHROPIC_API_KEY is not set, otherwise Anthropic.
    """
    from llm.client import get_client

    client = get_client()
    schema_dict = _build_schema_dict(output_schema)

    full_prompt = f"""{prompt}

Respond with ONLY valid JSON matching this schema:
{json.dumps(schema_dict, indent=2)}"""

    try:
        result = await client.complete_with_structured_output(
            full_prompt,
            output_schema,
            system=system,
            max_tokens=max_tokens,
        )
        return result
    except Exception as exc:
        logger.error("llm_utils.generate.error", error=str(exc))
        raise


def generate_text(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 2048,
) -> str:
    """Generate text output via LLM (sync wrapper for non-async contexts)."""
    import asyncio
    from llm.client import get_client

    client = get_client()

    async def _run() -> str:
        return await client.complete(prompt, system=system, max_tokens=max_tokens)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run())


def _build_schema_dict(schema_cls: type) -> dict[str, Any]:
    name = schema_cls.__name__
    if hasattr(schema_cls, "model_json_schema"):
        return {"name": name, "schema": schema_cls.model_json_schema()}
    elif hasattr(schema_cls, "schema"):
        return {"name": name, "schema": schema_cls.schema()}
    return {"name": name, "schema": {}}


def llm_system_prompt() -> str:
    return """You are a Stardew Valley Content Patcher mod generator.

You generate valid JSON for Content Patcher mod files.
Output ONLY JSON — no markdown, no explanation.
All paths use forward slashes (/) not backslashes.
Prices are in gold (g)."""