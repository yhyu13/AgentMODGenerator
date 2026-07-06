"""Unit tests for the achievements generator internals (v147).

Companion to ``test_achievements_phase.py`` (v146 pack wiring) and
``test_achievements_routing.py`` (v145 router keywords). Covers the
4 helpers, the ``validate_output`` branches on both generators, and
the LLM-failure fallback paths. Mirrors
``test_weather_event_generator.py``'s ``TestWeatherEventGeneratorFallback``.
Hermetic — no LLM, no DB, no Redis, no app.config.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.achievements import (
    AchievementDefinitionGenerator,
    AchievementRewardGenerator,
    _clamp_id,
    _normalize_icon_hint,
    _sanitize_achievement_id,
    _stable_hash_to_int,
)


# Stub prior_outputs envelope for the reward generator's input.
_REWARD_PRIOR = {
    "achievement_definition_generator": GeneratorOutput(
        files={
            "assets/achievements/achievements.json": {
                "achievements": [
                    {"AchievementID": "100", "Name": "First Harvest"},
                    {"AchievementID": "101", "Name": "Steady Hand"},
                ]
            }
        }
    )
}


class TestAchievementIdHelpers:
    """The id helpers are the rollup generator's contract — every id
    read from a prior generator passes through
    ``_sanitize_achievement_id`` before becoming a key in the Content
    Patcher ``Entries`` dict. A regression here silently misaligns
    keys across the 3 generators."""

    @pytest.mark.parametrize("raw,expected", [
        (None, "100"),       # default
        (250, "250"),        # in range int
        (0, "100"),          # below min
        (99999, "9999"),     # above max
        ("500", "500"),      # numeric string in range
        ("0", "100"), ("50000", "9999"),  # numeric string out of range
        ("", "100"), ("!@#$%", "100"),  # empty / non-alnum → default
    ])
    def test_sanitize_achievement_id_branches(self, raw, expected: str) -> None:
        assert _sanitize_achievement_id(raw) == expected

    def test_clamp_id_boundary(self) -> None:
        assert _clamp_id(100) == "100" and _clamp_id(500) == "500"
        assert _clamp_id(9999) == "9999"
        # Out-of-range ints: clamp to the nearest boundary.
        assert _clamp_id(0) == "100" and _clamp_id(10000) == "9999"

    def test_alphanumeric_hash_is_deterministic_and_clamped(self) -> None:
        # Same non-numeric input must hash to the same id every time
        # so the 3 generators agree without sharing state.
        a = _sanitize_achievement_id("mossy_milestone")
        assert a == _sanitize_achievement_id("mossy_milestone")
        assert 100 <= int(a) <= 9999

    def test_stable_hash_contract(self) -> None:
        # FNV-1a 32-bit: deterministic, fits in unsigned 32-bit,
        # distinct inputs almost always distinct.
        assert _stable_hash_to_int("test") == _stable_hash_to_int("test")
        assert _stable_hash_to_int("a") != _stable_hash_to_int("b")
        assert 0 <= _stable_hash_to_int("anything") <= 0xFFFFFFFF


class TestNormalizeIconHint:
    """The icon-hint normaliser gates every achievement's ``Icon``
    field. It MUST return one of the known safe keywords — any
    other value fails at runtime when SMAPI tries to load it."""

    @pytest.mark.parametrize("hint,expected", [
        ("fishing", "fishing"),
        ("FISHING", "fishing"),     # uppercased
        ("  Crops  ", "crops"),     # whitespace stripped
        ("invalid_icon", "crops"),  # not in safe set → default
        (None, "crops"),            # None → default
        (42, "crops"),              # non-string → default
        (["fishing"], "crops"),     # non-string list → default
    ])
    def test_normalize_icon_hint_branches(self, hint, expected: str) -> None:
        assert _normalize_icon_hint(hint) == expected  # type: ignore[arg-type]


class TestAchievementDefinitionValidateOutput:
    """Full branch coverage on
    ``AchievementDefinitionGenerator.validate_output``."""

    def test_missing_achievements_json_flags_error(self) -> None:
        assert any(
            "achievements.json" in e
            for e in AchievementDefinitionGenerator().validate_output(GeneratorOutput())
        )

    def test_non_dict_achievements_json_flags_error(self) -> None:
        # Bypass add_file's dict type signature by writing the bad
        # shape directly to out.files — the realistic case where a
        # future code path emits a non-dict payload.
        out = GeneratorOutput()
        out.files["assets/achievements/achievements.json"] = ["not", "a", "dict"]  # type: ignore[assignment]
        assert any(
            "must be a dict" in e
            for e in AchievementDefinitionGenerator().validate_output(out)
        )

    @pytest.mark.parametrize("bad_list", [
        {},                             # missing "achievements" key
        {"achievements": []},           # empty list
        {"achievements": "not a list"}, # non-list
    ])
    def test_achievements_list_shape_errors(self, bad_list: dict) -> None:
        out = GeneratorOutput()
        out.add_file("assets/achievements/achievements.json", bad_list)
        # The source's first check (`if not data`) catches {} and
        # returns the "missing" error before the list-shape check.
        # For non-empty bad_list, the list-shape check fires. The
        # test accepts either error message so the source's check
        # ordering doesn't break the test.
        errors = AchievementDefinitionGenerator().validate_output(out)
        assert any(
            "missing" in e or "achievements list missing" in e
            for e in errors
        ), f"bad_list={bad_list!r} errors={errors!r}"

    def test_missing_keys_in_entry_flags_error(self) -> None:
        out = GeneratorOutput()
        out.add_file("assets/achievements/achievements.json", {
            "achievements": [{"Description": "no id or name"}]
        })
        assert any(
            "missing AchievementID or Name" in e
            for e in AchievementDefinitionGenerator().validate_output(out)
        )

    def test_valid_payload_returns_no_errors(self) -> None:
        out = GeneratorOutput()
        out.add_file("assets/achievements/achievements.json", {
            "achievements": [
                {"AchievementID": "100", "Name": "First Harvest"},
                {"AchievementID": "101", "Name": "Steady Hand"},
            ]
        })
        assert AchievementDefinitionGenerator().validate_output(out) == []


class TestAchievementRewardValidateOutput:
    """Full branch coverage on
    ``AchievementRewardGenerator.validate_output``."""

    def test_missing_rewards_json_flags_error(self) -> None:
        assert any(
            "rewards.json" in e
            for e in AchievementRewardGenerator().validate_output(GeneratorOutput())
        )

    def test_non_dict_rewards_json_flags_error(self) -> None:
        out = GeneratorOutput()
        out.files["assets/achievements/rewards.json"] = ["not", "a", "dict"]  # type: ignore[assignment]
        assert any(
            "must be a dict" in e
            for e in AchievementRewardGenerator().validate_output(out)
        )

    def test_valid_payload_returns_no_errors(self) -> None:
        out = GeneratorOutput()
        out.add_file("assets/achievements/rewards.json", {
            "rewards": [{
                "AchievementID": "100", "Gold": 1000,
                "Items": [{"Name": "Parsnip Seeds", "Count": 10}],
                "FriendshipPoints": 0, "FriendshipTarget": "",
            }]
        })
        assert AchievementRewardGenerator().validate_output(out) == []


class TestAchievementGeneratorFallbacks:
    """The LLM-driven generators fall back to hardcoded payloads
    when ``generate_structured`` raises. Verify the fallback paths
    produce shippable Content Patcher content without ever calling
    the LLM. Mirrors ``TestWeatherEventGeneratorFallback``."""

    @pytest.fixture
    def empty_input(self) -> GeneratorInput:
        return {
            "prompt": "add a custom achievement for harvesting 100 ancient seeds",
            "hint": {}, "t2_feedback": "",
            "request_id": "req_test_ach_fb",
            "game": "stardew_valley",
            "prior_outputs": {},
        }

    def test_definition_generator_fallback_emits_2_achievements(
        self, empty_input: GeneratorInput,
    ) -> None:
        with patch(
            "generators.packs.stardew_valley.features.achievements.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            out = asyncio.run(AchievementDefinitionGenerator().generate(empty_input))
        ach = out.files["assets/achievements/achievements.json"]["achievements"]
        assert len(ach) == 2
        assert {a["Name"] for a in ach} == {"First Harvest", "Steady Hand"}
        assert out.metadata["achievement_count"] == 2
        assert out.metadata["first_achievement_id"] == "100"
        # Fallback payload must validate cleanly (T1 gate would
        # otherwise reject it).
        assert AchievementDefinitionGenerator().validate_output(out) == []

    def test_reward_generator_fallback_emits_2_rewards(
        self, empty_input: GeneratorInput,
    ) -> None:
        with patch(
            "generators.packs.stardew_valley.features.achievements.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            inp: GeneratorInput = {**empty_input, "prior_outputs": _REWARD_PRIOR}
            out = asyncio.run(AchievementRewardGenerator().generate(inp))
        rewards = out.files["assets/achievements/rewards.json"]["rewards"]
        assert len(rewards) == 2
        assert {r["AchievementID"] for r in rewards} == {"100", "101"}
        assert out.metadata["reward_count"] == 2
        assert AchievementRewardGenerator().validate_output(out) == []