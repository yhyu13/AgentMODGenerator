"""Phase-isolation regression test — every phase must produce a loadable zip.

Pins the dominant MVP failure pattern (claude audit §1.1): 7 of 10 phases
relied on ``prior.get("manifest_generator")`` from a sibling phase that
never runs in isolation, so any single-phase generation produced a
manifest-less, unloadable zip. This test runs EVERY phase standalone
(empty ``prior_outputs``) and asserts the packaged zip:

1. contains a valid ``manifest.json`` (all required CP fields)
2. passes the SMAPI static validator (``tests/smapi_validate``),
   which accepts CP 2.x object roots and checks FromFile references.

The tests run the deterministic fallback paths: no LLM provider is
configured in the test env (conftest unsets the keys), so every generator
exercises its no-LLM fallback — exactly what a real user hits when the
provider is down or unconfigured.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import generators.packager as packager_module
from generators.core import get_game_pack
from tests.smapi_validate import validate_manifest, validate_zip_contents

ALL_PHASES = [
    "shop_channel", "texture", "npc_schedule", "event_mod",
    "custom_crafting", "farm_expansion", "weather_event", "achievements",
    "weapon_definition", "tool_definition",
]


def _make_input(prompt: str, phase: str, request_id: str, prior: dict) -> dict:
    return {
        "prompt": prompt,
        "hint": {"game": "stardew_valley", "phase": phase, "generators": []},
        "request_id": request_id,
        "game": "stardew_valley",
        "prior_outputs": prior,
        "t2_feedback": "",
    }


async def _run_phase_standalone(phase: str, request_id: str) -> dict:
    pack = get_game_pack("stardew_valley")
    assert pack is not None
    pg = pack.get_generators(phase)
    outputs: dict = {}
    prior: dict = {}
    for gen_name in pg.execution_order:
        gen_cls = pack.get_generator(gen_name, phase)
        assert gen_cls is not None, f"{phase}: generator {gen_name} not registered"
        gen = gen_cls()
        output = await gen.generate(_make_input(
            f"make a {phase.replace('_', ' ')} mod for testing",
            phase,
            request_id,
            prior,
        ))
        outputs[gen_name] = output
        prior[gen_name] = output
    return outputs


@pytest.mark.parametrize("phase", ALL_PHASES)
def test_phase_standalone_produces_loadable_zip(phase, tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(packager_module, "_LOCAL_OUTPUT_DIR", str(tmp_path))

    outputs = asyncio.run(_run_phase_standalone(phase, f"req_{phase}"))

    all_files: dict = {}
    all_assets: list[str] = []
    for output in outputs.values():
        all_files.update(output.files)
        all_assets.extend(output.assets)

    # 1. Every phase must emit manifest.json (the audit's core fix).
    manifest = all_files.get("manifest.json")
    assert manifest is not None, f"{phase}: no manifest.json in standalone output"
    assert isinstance(manifest, dict), f"{phase}: manifest.json is not a dict"
    errors = validate_manifest(manifest)
    assert errors == [], f"{phase} manifest invalid: {errors}"

    # 2. content.json must use the CP 2.x object root when present.
    content = all_files.get("content.json")
    if content is not None:
        if isinstance(content, str):
            content = json.loads(content)
        if isinstance(content, dict):
            assert "Changes" in content, f"{phase}: content.json missing Changes"
        else:
            # Legacy list root tolerated, but the pack should emit object roots.
            assert isinstance(content, list), f"{phase}: content.json unexpected type {type(content).__name__}"

    # 3. The packaged zip must pass the SMAPI static validator.
    zip_key = packager_module.package(f"req_{phase}", all_files, all_assets)
    zip_path = Path(tmp_path) / "mods" / f"req_{phase}" / f"{zip_key.split('/')[-1]}"
    errors = validate_zip_contents(zip_path)
    assert errors == [], f"{phase} zip fails SMAPI validation: {errors}"


def test_every_phase_registers_manifest_generator():
    """Every phase must have a manifest-producing path in its execution order.

    - ``weather_event`` predates the shared generator: own
      ``weather_manifest_generator``.
    - ``weapon_definition`` / ``tool_definition`` emit manifest.json from
      inside their ContentJsonGenerator via the shared ``build_manifest_dict``
      helper (no dedicated manifest generator).
    - The 6 audit-flagged phases (shop_channel, texture, npc_schedule,
      event_mod, custom_crafting, farm_expansion, achievements) must
      register the shared ``manifest_generator``.

    The end-to-end manifest presence is verified by the parametrized
    loadable-zip test; this test pins the registration contract.
    """
    pack = get_game_pack("stardew_valley")
    assert pack is not None
    no_dedicated_manifest_gen = {"weather_event", "weapon_definition", "tool_definition"}
    for phase in ALL_PHASES:
        pg = pack.get_generators(phase)
        has_manifest_gen = (
            "manifest_generator" in pg.execution_order
            or any(name.endswith("manifest_generator") for name in pg.execution_order)
        )
        if phase in no_dedicated_manifest_gen:
            continue
        assert has_manifest_gen, (
            f"{phase}: no manifest-producing generator in execution order"
        )
        assert "manifest_generator" in pg.execution_order, (
            f"{phase}: no shared manifest_generator in execution order"
        )
        assert pack.get_generator("manifest_generator", phase) is not None, (
            f"{phase}: manifest_generator class not registered"
        )
