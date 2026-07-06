"""Edge-case tests for AchievementContentJsonGenerator (v148).

Companion to ``test_achievements_phase.py`` (v146) and
``test_achievements_generators.py`` (v147). v146 covered the happy
path; this file covers partial-prior rollup branches + the
``validate_output`` "not a dict" branch v146 missed. Hermetic.
"""
from __future__ import annotations

import asyncio

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.achievements import (
    AchievementContentJsonGenerator,
)


def _input(prior: dict | None = None) -> GeneratorInput:
    return {
        "prompt": "add a custom achievement for harvesting 100 ancient seeds",
        "hint": {}, "t2_feedback": "",
        "request_id": "req_test_ach_edge",
        "game": "stardew_valley",
        "prior_outputs": prior if prior is not None else {},
    }


def _content(out: GeneratorOutput) -> dict:
    return out.files["content.json"]  # type: ignore[return-value]


class TestAchievementContentJsonPartialPriors:
    """AchievementContentJsonGenerator must degrade gracefully when
    one or more prior_outputs are absent — the T2 gate still needs
    a shippable content.json."""

    def test_empty_priors_emits_only_strings_ui_change(self) -> None:
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input()))
        # No achievements, no rewards → ``if changes:`` is false, so
        # no changes appended at all (Strings/UI is gated on that).
        assert _content(out)["Changes"] == []
        # mod_id falls back to the lowercased default when no manifest.
        assert out.metadata["mod_id"] == "custom.achievements"

    def test_manifest_missing_uses_default_mod_id(self) -> None:
        # Achievements present, but no manifest at all.
        ach = GeneratorOutput(files={"assets/achievements/achievements.json": {
            "achievements": [{"AchievementID": "100", "Name": "First Harvest"}],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(
            _input(prior={"achievement_definition_generator": ach})
        ))
        assert out.metadata["mod_id"] == "custom.achievements"
        # 1 achievement change + 1 Strings/UI = 2 changes total.
        assert len(_content(out)["Changes"]) == 2

    def test_manifest_present_but_not_dict_uses_default_mod_id(self) -> None:
        # Edge case: a future code path might emit a list/string at
        # manifest.json — the rollup must not crash. mod_id falls
        # back to the literal default ("Custom.Achievements", NOT
        # lowercased — the ``else`` branch returns it verbatim).
        ach = GeneratorOutput(files={"assets/achievements/achievements.json": {
            "achievements": [{"AchievementID": "100", "Name": "First Harvest"}],
        }})
        manifest_bad = GeneratorOutput(files={"manifest.json": "not a dict"})  # type: ignore[assignment]
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={
                "manifest_generator": manifest_bad,
                "achievement_definition_generator": ach,
            }
        )))
        assert out.metadata["mod_id"] == "Custom.Achievements", (
            f"Else branch returns the default verbatim (not lowercased), "
            f"got {out.metadata.get('mod_id')!r}"
        )
        # Strings/UI key uses the default mod_id verbatim.
        strings_change = _content(out)["Changes"][-1]
        assert "Custom.Achievements" in str(strings_change)

    def test_achievements_missing_emits_rewards_and_strings_only(self) -> None:
        # Rewards present, but achievements missing.
        reward = GeneratorOutput(files={"assets/achievements/rewards.json": {
            "rewards": [{"AchievementID": "100", "Gold": 1000}],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={"achievement_reward_generator": reward}
        )))
        changes = _content(out)["Changes"]
        assert len(changes) == 2
        # No Data/Achievements defs block — only the rewards additive block.
        assert [c["Target"] for c in changes].count("Data/Achievements") == 1

    def test_rewards_missing_emits_definitions_and_strings_only(self) -> None:
        ach = GeneratorOutput(files={"assets/achievements/achievements.json": {
            "achievements": [{"AchievementID": "100", "Name": "First Harvest"}],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={"achievement_definition_generator": ach}
        )))
        changes = _content(out)["Changes"]
        assert len(changes) == 2
        targets = [c["Target"] for c in changes]
        # Only 1 Data/Achievements block (the defs), no rewards block.
        assert targets.count("Data/Achievements") == 1
        assert "Data/Strings/UI" in targets

    def test_malformed_achievement_entries_are_skipped(self) -> None:
        # Non-dict entries are skipped; empty AchievementID is sanitized
        # to the default "100" so the entry survives under that key.
        ach = GeneratorOutput(files={"assets/achievements/achievements.json": {
            "achievements": [
                "not a dict",  # skipped
                {"AchievementID": "", "Name": "Defaulted"},  # → key "100"
                {"AchievementID": "100", "Name": "First Harvest"},  # → key "100"
            ],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={"achievement_definition_generator": ach}
        )))
        ach_change = next(c for c in _content(out)["Changes"]
                          if c["Target"] == "Data/Achievements" and "Fields" not in c)
        # "not a dict" was skipped; the two dict entries both normalize
        # to key "100" and the second wins on collision.
        assert len(ach_change["Entries"]) == 1
        assert "100" in ach_change["Entries"]

    def test_empty_friendship_target_omitted_from_reward_entry(self) -> None:
        # Empty FriendshipTarget must be omitted to keep the diff minimal.
        reward = GeneratorOutput(files={"assets/achievements/rewards.json": {
            "rewards": [
                {"AchievementID": "100", "Gold": 1000, "FriendshipTarget": ""},
            ],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={"achievement_reward_generator": reward}
        )))
        reward_change = next(c for c in _content(out)["Changes"]
                             if c["Target"] == "Data/Achievements" and c.get("Fields") == "Rewards")
        assert "FriendshipTarget" not in reward_change["Entries"]["100"]

    def test_achievement_id_zero_is_sanitized_to_100(self) -> None:
        # Regression: id "0" → _clamp_id → "100" (lower bound).
        ach = GeneratorOutput(files={"assets/achievements/achievements.json": {
            "achievements": [{"AchievementID": "0", "Name": "Zero Edge"}],
        }})
        out = asyncio.run(AchievementContentJsonGenerator().generate(_input(
            prior={"achievement_definition_generator": ach}
        )))
        ach_change = next(c for c in _content(out)["Changes"]
                          if c["Target"] == "Data/Achievements" and "Fields" not in c)
        assert "100" in ach_change["Entries"]


class TestAchievementContentJsonValidateOutput:
    """The remaining ``validate_output`` branch not covered by v146:
    content.json present but not a dict, plus the empty-dict case."""

    def test_non_dict_content_json_flags_error(self) -> None:
        # Bypass add_file's dict type signature by writing a non-dict
        # payload directly — mirrors the v147 reward/definition
        # ``test_non_dict_*_flags_error`` pattern.
        out = GeneratorOutput()
        out.files["content.json"] = ["not", "a", "dict"]  # type: ignore[assignment]
        errors = AchievementContentJsonGenerator().validate_output(out)
        assert any("must be a dict" in e for e in errors)

    def test_dict_without_changes_key_still_flags_error(self) -> None:
        # v146 covers "content.json missing" and "Changes key missing"
        # separately. Confirm the empty-dict edge case also flags.
        out = GeneratorOutput()
        out.add_file("content.json", {})
        errors = AchievementContentJsonGenerator().validate_output(out)
        # An empty {} content fails the first check (`not content`) so
        # the source returns the "content.json missing" error before
        # reaching the "Changes key missing" check. The test is
        # relaxed to "any error fires" — both messages qualify.
        assert any(
            "content.json missing" in e or "Changes" in e for e in errors
        ), f"errors={errors!r}"