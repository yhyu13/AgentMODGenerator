"""Pack-level smoke tests for the achievements phase (Session 6 v146).

Companion to ``tests/test_achievements_routing.py`` (v145) which covered
the router keyword layer. This file covers the pack wiring: that
``StardewValleyPack.get_generators("achievements")`` returns the
3-generator tuple in the expected execution order, that all three
classes have correct ``name`` / ``phase`` / ``game`` declarations,
and that the deterministic ``AchievementContentJsonGenerator``
emits a valid ``content.json`` when given a populated prior_outputs
envelope (mirrors ``test_weather_event_generator.py``'s
``TestWeatherContentJsonGeneratorDeterministic``).

Hermetic — no LLM, no DB, no Redis, no app.config. Mirrors the
v82-v88 weather_event pattern.
"""
from __future__ import annotations

import asyncio

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.achievements import (
    AchievementContentJsonGenerator,
    AchievementDefinitionGenerator,
    AchievementRewardGenerator,
)


_ACHIEVEMENT_GENERATORS = (
    AchievementDefinitionGenerator,
    AchievementRewardGenerator,
    AchievementContentJsonGenerator,
)
_ACHIEVEMENT_NAMES = (
    "achievement_definition_generator",
    "achievement_reward_generator",
    "achievement_content_json_generator",
)


class TestAchievementsPackRegistration:
    """The achievements phase is registered in the stardew_valley pack."""

    def test_achievements_phase_listed(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack
        phases = StardewValleyPack.list_phases()
        assert "achievements" in phases, (
            f"StardewValleyPack.list_phases() must include 'achievements' "
            f"after the v144 port, got {phases}"
        )

    def test_achievements_get_generators_execution_order(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack
        pg = StardewValleyPack.get_generators("achievements")
        order = [g.name for g in pg.generators]
        assert order == list(_ACHIEVEMENT_NAMES), (
            f"Pack's get_generators('achievements') order = {order}, "
            f"expected the 3-generator "
            f"Definition→Reward→ContentJson order so each generator "
            f"can consume the prior's output"
        )


class TestAchievementsGeneratorBasics:
    """The 3 generator classes declare name/phase/game correctly and
    have a no-op validate_output on the empty-GeneratorOutput
    contract."""

    @pytest.mark.parametrize("cls", _ACHIEVEMENT_GENERATORS)
    def test_phase_and_game_declarations(self, cls: type) -> None:
        assert cls.phase == "achievements", (
            f"{cls.__name__}.phase must be 'achievements' (pack "
            f"registration contract), got {cls.phase!r}"
        )
        assert cls.game == "stardew_valley", (
            f"{cls.__name__}.game must be 'stardew_valley'"
        )
        assert isinstance(cls.name, str) and cls.name, (
            f"{cls.__name__}.name must be a non-empty string"
        )
        assert cls.name in _ACHIEVEMENT_NAMES, (
            f"{cls.__name__}.name = {cls.name!r} must be one of the "
            f"3 expected generator names: {_ACHIEVEMENT_NAMES}"
        )


class TestAchievementContentJsonGeneratorDeterministic:
    """AchievementContentJsonGenerator is the deterministic rollup —
    consumes the prior 2 generators' outputs and emits a
    content.json with EditData blocks. Test with a fully-populated
    prior_outputs envelope to verify the contract."""

    def _build_prior_outputs(self) -> dict:
        """Construct a complete prior_outputs dict matching the contract
        AchievementContentJsonGenerator reads (see _extract_*_from_prior
        helpers in the source)."""
        return {
            "manifest_generator": GeneratorOutput(
                files={
                    "manifest.json": {
                        "UniqueID": "TestAchievementsMod",
                        "Name": "Test Achievements Mod",
                    }
                }
            ),
            "achievement_definition_generator": GeneratorOutput(
                files={
                    "assets/achievements/achievements.json": {
                        "achievements": [
                            {
                                "AchievementID": "100",
                                "Name": "First Harvest",
                                "Description": "Harvest your very first crop.",
                                "IconHint": "crops",
                            },
                            {
                                "AchievementID": "101",
                                "Name": "Steady Hand",
                                "Description": "Catch 10 fish of any kind.",
                                "IconHint": "fishing",
                            },
                        ]
                    }
                }
            ),
            "achievement_reward_generator": GeneratorOutput(
                files={
                    "assets/achievements/rewards.json": {
                        "rewards": [
                            {
                                "AchievementID": "100",
                                "Gold": 1000,
                                "Items": [{"Name": "Parsnip Seeds", "Count": 10}],
                                "FriendshipPoints": 0,
                                "FriendshipTarget": "",
                            },
                            {
                                "AchievementID": "101",
                                "Gold": 2500,
                                "Items": [{"Name": "Bamboo Pole", "Count": 1}],
                                "FriendshipPoints": 100,
                                "FriendshipTarget": "Willy",
                            },
                        ]
                    }
                }
            ),
        }

    def test_emits_content_json_with_3_change_blocks(self) -> None:
        gen = AchievementContentJsonGenerator()
        prior = self._build_prior_outputs()
        inp: GeneratorInput = {
            "prompt": "add a custom achievement for harvesting 100 ancient seeds",
            "hint": {}, "t2_feedback": "",
            "request_id": "req_test_achievements_1",
            "game": "stardew_valley",
            "prior_outputs": prior,
        }
        out = asyncio.run(gen.generate(inp))
        assert "content.json" in out.files, (
            f"AchievementContentJsonGenerator must emit content.json, "
            f"got files = {list(out.files.keys())}"
        )
        content = out.files["content.json"]
        assert isinstance(content, dict)
        assert "Changes" in content
        assert isinstance(content["Changes"], list)
        # 2 EditData blocks for Data/Achievements (definitions + rewards
        # additive Fields) + 1 EditData block for the Strings/UI
        # registration = 3 changes total.
        assert len(content["Changes"]) == 3, (
            f"Expected 3 changes (Data/Achievements defs + Data/Achievements "
            f"rewards + Data/Strings/UI), got {len(content['Changes'])}"
        )
        # Verify the mod_id propagated from manifest.
        assert out.metadata.get("mod_id") == "testachievementsmod", (
            f"AchievementContentJsonGenerator should lowercase the "
            f"manifest's UniqueID, got {out.metadata.get('mod_id')!r}"
        )

    def test_validates_missing_content_json(self) -> None:
        gen = AchievementContentJsonGenerator()
        empty_out = GeneratorOutput()
        errors = gen.validate_output(empty_out)
        assert any("content.json" in e for e in errors), (
            f"validate_output should flag missing content.json, "
            f"got errors = {errors}"
        )

    def test_validates_changes_key_missing(self) -> None:
        gen = AchievementContentJsonGenerator()
        out = GeneratorOutput()
        out.add_file("content.json", {"Format": "1.29.0"})  # no "Changes"
        errors = gen.validate_output(out)
        assert any("Changes" in e for e in errors), (
            f"validate_output should flag missing Changes key, "
            f"got errors = {errors}"
        )