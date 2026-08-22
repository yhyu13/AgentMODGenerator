"""T1 game-data checks for general_author — the three on-disk LLM mods.

The isolated SMAPI load of ``LLM llm_schema_{1,2,3}`` proved the packs
*load* (CP accepts the JSON) and still fail in-game: Fields.Fish wipes
the mountain spawn list, Data/Machines keys lack ``(BC)``, recipes use
display names, Data/WeatherEvents is not a game asset. T1 previously
only checked Action/Target presence, so those mods passed the gate.

Fixtures are copies of the three installed content.json files. T1 must
FAIL them. A well-formed machine+append-fish change set must still PASS.
"""
from __future__ import annotations

import json
from pathlib import Path

from generators.core import GeneratorOutput
from quality.gate_t1 import run_t1

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "llm_schema_mods"


def _ga_out(content: dict) -> GeneratorOutput:
    out = GeneratorOutput()
    out.add_file(
        "manifest.json",
        {
            "Name": "fixture",
            "Author": "AI Generator",
            "Version": "1.0.0",
            "UniqueID": "ai_generator.fixture",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        },
    )
    out.add_file("content.json", content)
    return out


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _t1(content: dict):
    return run_t1("req_ga_gamedata", {"general_author_generator": _ga_out(content)})


class TestOnDiskLlmModsFailT1:
    def test_schema_1_fish_fields_wipe_rejected(self) -> None:
        result = _t1(_load_fixture("llm_schema_1_content.json"))
        assert result.passed is False
        assert any("Fish" in e and "Locations" in e for e in result.errors)

    def test_schema_2_machine_unqualified_key_rejected(self) -> None:
        result = _t1(_load_fixture("llm_schema_2_content.json"))
        assert result.passed is False
        assert any("(BC)" in e for e in result.errors)

    def test_schema_2_recipe_display_names_rejected(self) -> None:
        result = _t1(_load_fixture("llm_schema_2_content.json"))
        assert result.passed is False
        assert any("ingredient" in e.lower() for e in result.errors)

    def test_schema_3_weather_events_rejected(self) -> None:
        result = _t1(_load_fixture("llm_schema_3_content.json"))
        assert result.passed is False
        assert any("WeatherEvents" in e for e in result.errors)

    def test_schema_3_recipe_display_names_rejected(self) -> None:
        result = _t1(_load_fixture("llm_schema_3_content.json"))
        assert result.passed is False
        assert any("ingredient" in e.lower() for e in result.errors)


class TestRegenLlmModsPassT1:
    """After the schema/prompt/T1 fix, regenerated packs must pass T1."""

    def test_regen_schema_1_passes(self) -> None:
        result = _t1(_load_fixture("llm_schema_1_regen.json"))
        assert result.passed, result.errors

    def test_regen_schema_2_passes(self) -> None:
        result = _t1(_load_fixture("llm_schema_2_regen.json"))
        assert result.passed, result.errors

    def test_regen_schema_3_passes(self) -> None:
        result = _t1(_load_fixture("llm_schema_3_regen.json"))
        assert result.passed, result.errors


class TestWellFormedStillPasses:
    def test_qualified_machine_and_numeric_recipe_and_targetfield_fish(self) -> None:
        content = {
            "Format": "2.0.0",
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/BigCraftables",
                    "Entries": {
                        "stone_smelter": {
                            "Name": "Stone Smelter",
                            "DisplayName": "Stone Smelter",
                            "Description": "Turns stone into gold ore.",
                            "Texture": "TileSheets/Craftables",
                            "SpriteIndex": 13,
                        }
                    },
                },
                {
                    "Action": "EditData",
                    "Target": "Data/Machines",
                    "Entries": {
                        "(BC)stone_smelter": {
                            "OutputRules": [
                                {
                                    "Id": "Default",
                                    "Triggers": [
                                        {
                                            "Trigger": "ItemPlacedInMachine",
                                            "RequiredItemId": "(O)390",
                                            "RequiredCount": 10,
                                        }
                                    ],
                                    "OutputItem": [{"ItemId": "(O)384", "MinStack": 1}],
                                    "MinutesUntilReady": 60,
                                }
                            ]
                        }
                    },
                },
                {
                    "Action": "EditData",
                    "Target": "Data/CraftingRecipes",
                    "Entries": {
                        "Stone Smelter": "390 50 378 20/Home/stone_smelter/true/default"
                    },
                },
                {
                    "Action": "EditData",
                    "Target": "Data/Locations",
                    "TargetField": ["Mountain", "Fish"],
                    "Entries": {
                        "ai_generator.luminous_carp_entry": {
                            "Id": "ai_generator.luminous_carp_entry",
                            "ItemId": "ai_generator.luminous_carp",
                            "Chance": 0.12,
                        }
                    },
                },
            ],
        }
        result = _t1(content)
        assert result.passed, result.errors


class TestSyntheticRejects:
    def test_buffdata_target_rejected(self) -> None:
        content = {
            "Format": "2.0.0",
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/BuffData",
                    "Entries": {"x": {}},
                }
            ],
        }
        result = _t1(content)
        assert result.passed is False
        assert any("BuffData" in e for e in result.errors)

    def test_machines_targetfield_does_not_require_bc_on_rule_id(self) -> None:
        content = {
            "Format": "2.0.0",
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/Machines",
                    "TargetField": ["(BC)15", "OutputRules"],
                    "Entries": {"custom_rule": {"Id": "custom_rule"}},
                }
            ],
        }
        result = _t1(content)
        assert result.passed, result.errors
