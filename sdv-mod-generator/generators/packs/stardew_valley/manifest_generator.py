"""Shared Content Patcher manifest generator.

Every phase in the Stardew Valley pack must produce a ``manifest.json``
or the generated zip is silently rejected by SMAPI — the pack's own
``content.json`` and assets are valid but the mod never appears in-game.

History (2026-08-01, MVP audit): the ``texture``, ``event_mod``,
``custom_crafting``, and ``achievements`` phases registered no manifest
generator at all, and their ``content.json`` composers read
``prior.get("manifest_generator")`` from a sibling phase that never runs
in isolation — so any single-phase generation produced a manifest-less,
unloadable zip. ``npc_schedule`` / ``farm_expansion`` reused
``shop_channel``'s LLM-driven ``ManifestGenerator``; this module is the
single shared, deterministic implementation every phase registers
instead.

The generator is deliberately LLM-free: the mod name / description are
derived from the prompt via the shared ``build_manifest_dict`` helper
(the same shape ``weapon_definition`` / ``tool_definition`` emit), so a
single-phase run always yields a loadable manifest even with no LLM
provider configured.
"""
from __future__ import annotations

import structlog

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.core.manifest import (
    build_manifest_dict,
    fallback_name_from_prompt,
    slugify_unique_id,
)

logger = structlog.get_logger(__name__)


class ManifestGenerator(BaseGenerator):
    name = "manifest_generator"
    phase = "shared"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = inp["prompt"]
        unique_id = slugify_unique_id(prompt)
        name = fallback_name_from_prompt(prompt)
        description = f"A Content Patcher mod for Stardew Valley, generated from: {prompt[:140]}"
        out.add_file("manifest.json", build_manifest_dict(unique_id, name, description))
        out.metadata["mod_slug"] = unique_id
        logger.info(
            "manifest_generator.done",
            request_id=inp["request_id"],
            unique_id=unique_id,
            name=name,
        )
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        manifest = output.files.get("manifest.json")
        if not manifest:
            errors.append("manifest_generator: manifest.json missing")
            return errors
        for f in ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]:
            if f not in manifest:
                errors.append(f"manifest_generator: missing '{f}'")
        return errors
