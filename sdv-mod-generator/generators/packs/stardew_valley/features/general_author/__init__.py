"""General Content Patcher author — the hybrid 'unbounded' layer.

The 10 template phases cover the well-understood mod families (shops,
textures, npc schedules, events, crafting, farm expansion, weather,
achievements, weapons, tools). Every OTHER prompt routes here: a single
LLM-driven generator that writes arbitrary Content Patcher 2.x changes
(any SDV 1.6 ``Data/`` asset, Load, Include) the way a human mod author
would — using the same reference material (standards + content_actions
knowledge) and relying on the downstream gates (T1 generic CP schema
checks, the static validator, and the real-SMAPI load test) to catch
mistakes.

Deterministic behavior:
- With ``GENERAL_AUTHOR_DETERMINISTIC=1``: emits a fixed, loadable sample
  mod (used by tests and the real-game gate without LLM keys).
- Otherwise: requires an LLM provider. When none is configured the
  generator raises and the pipeline fails with a clear
  "general_author requires an LLM provider" error — never silent garbage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.core.manifest import build_manifest_dict, slugify_unique_id
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger(__name__)

_KB_DIR = Path(__file__).parent.parent.parent / "knowledge"


class GeneralAuthorChange(BaseModel):
    """One Content Patcher action (EditData/Load/Include/...)."""

    Action: str
    Target: str
    Entries: dict[str, Any] | None = None
    Fields: dict[str, Any] | None = None
    FromFile: str | None = None
    When: dict[str, Any] | None = None
    Priority: str | None = None


class GeneralAuthorContent(BaseModel):
    """content.json object root (CP 2.x)."""

    Format: str = "2.0.0"
    Changes: list[GeneralAuthorChange]


class GeneralAuthorOutput(BaseModel):
    """LLM contract: manifest name/description + content.json changes."""

    Name: str
    Description: str
    content: GeneralAuthorContent


def _data_schemas_section() -> str:
    """Render the verified SDV 1.6 data-schema reference into prompt text.

    Loads ``knowledge/data_schemas.json`` (shapes proven loadable in the
    real game) so the general author emits records the game's parser
    actually accepts instead of guessing pipe/object formats from memory.
    """
    path = _KB_DIR / "data_schemas.json"
    try:
        schemas = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("general_author.data_schemas_missing", error=str(exc))
        return "[data_schemas.json unavailable]"
    lines: list[str] = [schemas.get("title", "SDV 1.6 data schemas"), "Rules:"]
    lines.extend(f"- {rule}" for rule in schemas.get("rules", []))
    for asset, spec in (schemas.get("assets") or {}).items():
        value = spec.get("value", "")
        lines.append(f"- {asset}: {value}")
        if spec.get("format"):
            lines.append(f"    format: {spec['format']}")
        if spec.get("fields"):
            lines.append("    fields: " + ", ".join(spec["fields"]))
        if spec.get("example"):
            lines.append(f"    example: {spec['example']}")
        if spec.get("note"):
            lines.append(f"    note: {spec['note']}")
    return "\n".join(lines)


def _general_author_system_prompt() -> str:
    return (
        llm_system_prompt()
        + "\n\nYou are a GENERAL Content Patcher author. The user's request is "
        "not covered by the template phases (TV shopping, textures, npc "
        "schedules, events, crafting, farm expansion, weather, achievements, "
        "weapons, tools). Design ANY valid Content Patcher 2.x change set that "
        "fulfills the request using the SDV 1.6 data vocabulary: EditData on "
        "game Data/ assets (e.g. Data/Characters, Data/Fish, Data/Monsters, "
        "Data/Machines, Data/Objects, Data/CraftingRecipes, Data/BuffData, "
        "Characters/Dialogue, Characters/schedules), or Load from existing "
        "game files. Keep the change set minimal, loadable, and internally "
        "consistent (e.g. a new Data/Fish entry needs its matching rows). "
        "Reuse existing object IDs where sensible.\n\n"
        "The data schemas below are VERIFIED against the real game — follow "
        "them exactly (string vs object value, field order, delimiters):\n"
        + _data_schemas_section()
    )


def _deterministic_sample() -> dict[str, Any]:
    """A fixed, real-game-loadable CP mod used when no LLM is configured
    (tests / real-game gate only — never a substitute for an answer)."""
    return {
        "Format": "2.0.0",
        "Changes": [
            {
                "Action": "EditData",
                "Target": "Data/Achievements",
                "Entries": {
                    "999999": "General Author^A sample achievement from the general author.^true^-1^0"
                },
            }
        ],
    }


class GeneralAuthorGenerator(BaseGenerator):
    name = "general_author_generator"
    phase = "general_author"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        if os.environ.get("GENERAL_AUTHOR_DETERMINISTIC") == "1":
            logger.info(
                "general_author_generator.deterministic",
                request_id=inp["request_id"],
            )
            return self._emit(
                inp,
                name="General Author Sample",
                description=(
                    "A deterministic sample Content Patcher mod (no LLM "
                    "configured; GENERAL_AUTHOR_DETERMINISTIC=1)."
                ),
                content=_deterministic_sample(),
            )

        try:
            result = await generate_structured(
                f"User request: {inp['prompt']}\n\n"
                "Emit the Content Patcher mod (manifest Name/Description and "
                "content.json Changes) that fulfills this request.",
                GeneralAuthorOutput,
                system=_general_author_system_prompt(),
                max_tokens=4096,
            )
            model = GeneralAuthorOutput(**result)
            logger.info(
                "general_author_generator.done",
                request_id=inp["request_id"],
                changes=len(model.content.Changes),
            )
            return self._emit(
                inp,
                name=model.Name,
                description=model.Description,
                content=model.content.model_dump(exclude_none=True),
            )
        except Exception as exc:
            logger.error(
                "general_author_generator.failed",
                request_id=inp["request_id"],
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise RuntimeError(
                f"general_author requires an LLM provider: {exc}"
            ) from exc

    def _emit(
        self,
        inp: GeneratorInput,
        name: str,
        description: str,
        content: dict[str, Any],
    ) -> GeneratorOutput:
        out = GeneratorOutput()
        unique_id = slugify_unique_id(inp["prompt"])
        desc = description or (
            f"A Content Patcher mod for Stardew Valley, generated from: "
            f"{inp['prompt'][:140]}"
        )
        out.add_file("manifest.json", build_manifest_dict(unique_id, name, desc))
        out.add_file("content.json", content)
        out.metadata["general_author"] = True
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        manifest = output.files.get("manifest.json")
        if not isinstance(manifest, dict):
            errors.append("general_author_generator: manifest.json missing")
        content = output.files.get("content.json")
        if not isinstance(content, dict):
            errors.append("general_author_generator: content.json missing")
        elif not isinstance(content.get("Changes"), list):
            errors.append("general_author_generator: content.json missing 'Changes'")
        return errors
