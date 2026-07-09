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
    max_retries: int = 2,
    base_delay: float = 1.0,
) -> dict[str, Any]:
    """Generate structured JSON output via LLM with exponential backoff retry.

    Uses OpenAI if ANTHROPIC_API_KEY is not set, otherwise Anthropic.
    Uses native structured output when available; schema is NOT added
    to the prompt text since the client handles it via response_format.

    If validation fails because the LLM wrapped output in a schema-name key,
    automatically unwraps and retries validation once.

    Retries on transient errors (RuntimeError, IOError) with exponential
    backoff to improve pipeline robustness against temporary LLM failures.
    """
    global _client
    if _client is None:
        async with _get_async_client_lock():
            if _client is None:
                from llm.client import get_client
                _client = get_client()
    client = _client

    schema_name = output_schema.__name__
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
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
        except (RuntimeError, IOError) as exc:
            last_exception = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "llm_utils.generate.retry",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            else:
                logger.error("llm_utils.generate.error", error=str(exc))
                raise
        except Exception as exc:
            logger.error("llm_utils.generate.error", error=str(exc))
            raise

    if last_exception:
        raise last_exception
    raise RuntimeError("generate_structured: unexpected exit from retry loop")


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
    """Return the canonical system prompt for the LLM.

    The prompt is a short base string (5 lines) plus a bundled copy of
    the STARDEW_VALLEY_MOD_STANDARDS.md document at
    ``docs/STARDEW_VALLEY_MOD_STANDARDS.md`` (when the file exists in
    the project root). The standards doc is the single source of truth
    for what a high-quality mod looks like and is derived from
    actual T2 judge feedback on previously generated mods. The LLM is
    expected to read and apply these standards before producing any
    content (manifest.json, events, dialogue, buffs, mail).

    The file is read on every call rather than cached so doc edits
    take effect immediately without a process restart — useful when
    iterating on the standards during a development session.
    """
    base = (
        "You are a Stardew Valley Content Patcher mod generator.\n\n"
        "You generate valid JSON for Content Patcher mod files following the\n"
        "quality standards in the bundled STARDEW_VALLEY_MOD_STANDARDS.md document.\n\n"
        "The full standards doc is included below — read it before producing output:\n\n"
    )
    standards_path = Path(__file__).parent.parent / "docs" / "STARDEW_VALLEY_MOD_STANDARDS.md"
    if standards_path.exists():
        try:
            standards = standards_path.read_text(encoding="utf-8")
        except OSError:
            standards = (
                "\n[STARDEW_VALLEY_MOD_STANDARDS.md is unreadable; "
                "fall back to base instructions.]\n"
            )
    else:
        standards = (
            "\n[STARDEW_VALLEY_MOD_STANDARDS.md is missing from docs/; "
            "fall back to base instructions. Manifest.json + content.json + "
            "valid EditData actions are required for a high-scoring mod.]\n"
        )
    trailer = (
        "\n\nOutput ONLY valid JSON matching the expected schema — no markdown, no\n"
        "explanation. All paths use forward slashes (/) not backslashes. Prices are\n"
        "in gold (g)."
    )
    return base + standards + trailer