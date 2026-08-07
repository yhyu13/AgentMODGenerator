"""Boundary probe: route + generate + validate peculiar SDV mod prompts.

Two tiers:
- tier1: quirky prompts whose concept IS reachable by an existing phase.
- tier2: prompts for concepts the router has NO keyword for (quests, crops,
  fish, monsters, machines, skills, loose npc phrasing) - expected to
  misroute to shop_channel.

Runs the real pipeline (route -> generate -> T1 -> T2 -> package) with no
LLM keys (deterministic fallback), static-validates each zip, and writes
``mods/.boundary/report.json`` plus per-mod zips under ``mods/.boundary/mods/``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

_TARGET = _PKG_DIR.parent / "mods" / ".boundary"
_LOCAL_OUTPUT_DIR = _TARGET / "outputs"
os.environ["LOCAL_OUTPUT_DIR"] = str(_LOCAL_OUTPUT_DIR)

BOUNDARY_PROMPTS: list[tuple[str, str, str]] = [
    # (label, tier, prompt)
    ("shop_cursed_tv", "tier1", "make a cursed TV channel that sells a random item at 3am"),
    ("shop_pyramid", "tier1", "a pyramid scheme TV shopping channel that mails items you never ordered"),
    ("npc_rave", "tier1", "add an npc schedule where Linus throws a rave in the mines every Friday night"),
    ("npc_lemonade", "tier1", "add a new daily schedule and dialogue for the wizard running a lemonade stand"),
    ("event_danceoff", "tier1", "add a festival where you can battle Mayor Lewis in a dance-off"),
    ("crafting_sandwich", "tier1", "add a custom crafting recipe for a Linus Sandwich that instantly maxes friendship"),
    ("crafting_bomb", "tier1", "add a crafting recipe for a Joja cola bomb"),
    ("farm_skyisland", "tier1", "add a farm expansion sky island building with a warp to the mountain"),
    ("weather_meteor", "tier1", "add a meteor shower weather event that rains starfruit every night"),
    ("weather_bloodmoon", "tier1", "add a weather event with a blood moon storm that grants a buff"),
    ("achieve_sleepless", "tier1", "add an achievement for going 100 days without sleeping"),
    ("weapon_rubberchicken", "tier1", "add a rubber chicken weapon that does 1 damage but gives +999 speed"),
    ("tool_watering", "tier1", "add a custom watering can tool that waters a 9x9 area"),
    ("texture_potato", "tier1", "replace the cat sprite with a potato"),
    ("quest_amulet", "tier2", "add a quest to find the ancient amulet deep in the mines"),
    ("crop_growth", "tier2", "make crops grow twice as fast during summer"),
    ("fish_mine", "tier2", "add a custom fish that can be caught in the mines"),
    ("monster_golem", "tier2", "add a new stone golem monster to the mines"),
    ("machine_transmute", "tier2", "add a new machine that turns stone into gold"),
    ("skill_mastery", "tier2", "add a new fishing mastery skill to the game"),
    ("npc_forge", "tier2", "give the blacksmith Clint a new forge schedule"),
    ("event_snowcandy", "tier2", "add a festival where it snows candy in the town square"),
]


def route_only(prompt: str) -> str:
    from orchestrator.router import route
    phase, _hint = route(prompt)
    return phase


async def generate_one(label: str, prompt: str) -> dict:
    from orchestrator.pipeline import run_pipeline

    request_id = f"boundary_{label}"
    result = await run_pipeline(request_id, "boundary", prompt)
    return {
        "label": label,
        "prompt": prompt,
        "request_id": request_id,
        "routed_phase": result.phase,
        "status": result.status,
        "t1_passed": result.t1_passed,
        "t2_available": result.t2_available,
        "zip_key": result.zip_key,
        "generators_failed": result.generators_failed,
        "errors": result.errors,
    }


def copy_and_validate(summary: dict) -> dict:
    request_id = summary["request_id"]
    zip_path = _LOCAL_OUTPUT_DIR / "mods" / request_id / f"{request_id}.zip"

    mods_dir = _TARGET / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        flat = mods_dir / f"{request_id}.zip"
        shutil.copyfile(zip_path, flat)
        summary["zip"] = str(flat.relative_to(_TARGET))
        try:
            from tests.smapi_validate import validate_zip_contents
            summary["validator_errors"] = validate_zip_contents(zip_path)
        except Exception as exc:
            summary["validator_errors"] = [f"validator crashed: {type(exc).__name__}: {exc}"]
    else:
        summary["zip"] = None
        summary["validator_errors"] = ["zip missing"]
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Boundary probe: route + generate peculiar SDV prompts")
    parser.add_argument("--tier", choices=["tier1", "tier2"], help="only probe one tier (additive)")
    args = parser.parse_args()

    if args.tier:
        prompts = [p for p in BOUNDARY_PROMPTS if p[1] == args.tier]
        _TARGET.mkdir(parents=True, exist_ok=True)
    else:
        if _TARGET.exists():
            shutil.rmtree(_TARGET)
        _TARGET.mkdir(parents=True)
        prompts = BOUNDARY_PROMPTS

    results: list[dict] = []
    for label, tier, prompt in prompts:
        routed = route_only(prompt)
        summary = await generate_one(label, prompt)
        summary["tier"] = tier
        summary["route_only_phase"] = routed
        summary = copy_and_validate(summary)
        results.append(summary)
        print(f"[{tier:5s}] {label:22s} routed={summary['routed_phase']:18s} "
              f"status={summary['status']:6s} t1={summary['t1_passed']!s:5s} "
              f"validator={len(summary.get('validator_errors', []))}")

    report_name = f"report_{args.tier}.json" if args.tier else "report.json"
    (_TARGET / report_name).write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    shutil.rmtree(_LOCAL_OUTPUT_DIR, ignore_errors=True)
    print(f"\nreport: {_TARGET / report_name}")


if __name__ == "__main__":
    asyncio.run(main())
