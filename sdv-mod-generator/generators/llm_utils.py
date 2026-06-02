"""LLM generation utilities for generators."""
import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

logger = structlog.get_logger()

_KB_DIR = Path(__file__).parent.parent / "knowledge" / "data"

_item_ids: dict[str, Any] | None = None
_game_systems: dict[str, Any] | None = None
_content_actions: dict[str, Any] | None = None

_client: Any = None
_client_lock = threading.Lock()
_async_client_lock: asyncio.Lock | None = None


def _get_async_client_lock() -> asyncio.Lock:
    global _async_client_lock
    if _async_client_lock is None:
        _async_client_lock = asyncio.Lock()
    return _async_client_lock


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


def _get_cached_client() -> Any:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from llm.client import get_client
                _client = get_client()
    return _client


def _unwrap_schema_wrapper(result: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """If LLM wrapped output in a schema-name key, extract the inner dict.

    E.g. {"ShopItemPoolOutput": {"items": [...]}} → {"items": [...]}
    Handles case variations: "ShopItemPoolOutput", "shop_item_pool_output", etc.
    """
    if len(result) != 1:
        return result
    key = next(iter(result))
    # Check if key is a schema-name wrapper (PascalCase or snake_case variant of schema name)
    schema_root = schema_name.replace("Output", "").replace("output", "")
    key_root = key.replace("Output", "").replace("output", "")
    if key_root.lower() == schema_root.lower():
        inner = result[key]
        if isinstance(inner, dict):
            return inner
    return result


async def generate_structured(
    prompt: str,
    output_schema: type,
    system: str | None = None,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Generate structured JSON output via LLM.

    Uses OpenAI if ANTHROPIC_API_KEY is not set, otherwise Anthropic.
    Uses native structured output when available; schema is NOT added
    to the prompt text since the client handles it via response_format.

    If validation fails because the LLM wrapped output in a schema-name key,
    automatically unwraps and retries validation once.
    """
    global _client
    if _client is None:
        async with _get_async_client_lock():
            if _client is None:
                from llm.client import get_client
                _client = get_client()
    client = _client

    schema_name = output_schema.__name__

    try:
        result = await client.complete_with_structured_output(
            prompt,
            output_schema,
            system=system,
            max_tokens=max_tokens,
        )
        # Validate; if it fails due to schema-name wrapper, unwrap and retry
        try:
            output_schema(**result)
        except ValidationError:
            unwrapped = _unwrap_schema_wrapper(result, schema_name)
            try:
                output_schema(**unwrapped)
                result = unwrapped
            except ValidationError:
                logger.warning(
                    "llm_utils.generate.unwrapped_validation_failed",
                    schema=schema_name,
                    original=result,
                    unwrapped=unwrapped,
                )
                raise
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
    from concurrent.futures import ThreadPoolExecutor
    from llm.client import get_client

    async def _run() -> str:
        return await get_client().complete(prompt, system=system, max_tokens=max_tokens)

    def _run_sync() -> str:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_sync)
        return future.result(timeout=120)


def llm_system_prompt() -> str:
    return """You are a Stardew Valley Content Patcher mod generator.

You generate valid JSON for Content Patcher mod files.
Output ONLY JSON — no markdown, no explanation.
All paths use forward slashes (/) not backslashes.
Prices are in gold (g)."""