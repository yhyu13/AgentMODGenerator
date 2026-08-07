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

import os
from typing import Any

import structlog
from pydantic import BaseModel

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.core.manifest import build_manifest_dict, slugify_unique_id
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger(__name__)


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
        "SDV 1.6 DATA FORMAT RULES (these are enforced by the real game — "
        "violating them fails the SMAPI load gate):\n"
        "- Typed Data/ assets such as Data/Objects, Data/Locations, "
        "Data/Characters expect JSON OBJECT entry values matching the C# "
        "property names (e.g. an ObjectData object: {\"Name\", "
        "\"DisplayName\", \"Description\", \"Type\", \"Category\", \"Price\", "
        "\"Texture\", \"SpriteIndex\", \"Edibility\", \"ContextTags\"}). Do "
        "NOT emit pipe-delimited strings for these — the game can't convert "
        "them.\n"
        "- Data/Fish accepts a pipe-delimited entry string.\n"
        "- AVOID editing Data/Locations unless you know its full LocationData "
        "object shape; a malformed record breaks the whole asset. Prefer "
        "leaving it untouched.\n"
        "- EditData 'Fields' keys must be INTEGER pipe indices (e.g. \"0\", "
        "\"1\"). NEVER use a field NAME (like \"Fish\" or \"Name\") as a "
        "Fields key — the game rejects it.\n"
        "- NPC dialogue lives in Characters/Dialogue/<NpcName> (NOT under a "
        "Data/ prefix). Schedules are Characters/schedules/<NpcName>.\n"
        "- content.json 'Format' must be '2.0.0' or newer (never the legacy "
        "1.x format version)."
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
