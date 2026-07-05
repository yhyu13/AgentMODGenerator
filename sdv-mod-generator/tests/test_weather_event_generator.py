"""Tests for the weather_event generator (Session 6 v88 port).

The weather_event generator produces 5 generators:
- WeatherEventGenerator: LLM-driven weather events
- WeatherNPCDialogueGenerator: weather-dependent NPC dialogue
- WeatherBuffGenerator: weather buffs
- WeatherMailGenerator: weather announcement mails
- WeatherContentJsonGenerator: rolls up all the above into content.json

The first 4 use the LLM (via generate_structured). The 5th is
purely deterministic — it consumes the prior outputs and emits
a content.json with the EditData blocks. The deterministic path
is what we test here; the LLM-driven path is tested by the smoke
test (the generator returns a fallback when the LLM fails).

This file is hermetic — it does not import app.config, does not
talk to Postgres/Redis/LLM, and runs in < 100ms. Mirrors the
hermetic-test pattern the cron established for the v82-v86
generator port batch.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.weather_event import (
    WeatherBuffGenerator,
    WeatherContentJsonGenerator,
    WeatherEventGenerator,
    WeatherMailGenerator,
    WeatherNPCDialogueGenerator,
)


class TestWeatherEventGeneratorBasics:
    """The 5 generator classes instantiate, declare name/phase/game,
    and have a no-op validate_output on the empty-GeneratorOutput
    contract (each generator overrides validate_output to check
    specific files; the empty case is the negative test)."""

    def test_all_five_classes_exist(self) -> None:
        # Phase-registration completeness check: every generator
        # the pack's get_generators("weather_event") returns must be
        # importable from this module.
        for cls in (
            WeatherEventGenerator,
            WeatherNPCDialogueGenerator,
            WeatherBuffGenerator,
            WeatherMailGenerator,
            WeatherContentJsonGenerator,
        ):
            assert cls.phase == "weather_event", (
                f"{cls.__name__}.phase must be 'weather_event' (pack "
                f"registration contract), got {cls.phase!r}"
            )
            assert cls.game == "stardew_valley", (
                f"{cls.__name__}.game must be 'stardew_valley'"
            )
            assert isinstance(cls.name, str) and cls.name, (
                f"{cls.__name__}.name must be a non-empty string"
            )

    def test_execution_order_matches_name_order(self) -> None:
        # The pack's get_generators("weather_event") returns these
        # generators in a specific execution_order. The cron archive's
        # v44 work established this convention. Verify the
        # execution_order matches the generator name order.
        # The pack's get_generators is on master; we import it lazily
        # to avoid import-time circular issues.
        from generators.packs.stardew_valley import StardewValleyPack
        pg = StardewValleyPack.get_generators("weather_event")
        # pg.generators is a tuple of class references (PhaseGenerators
        # stores them as classes). Convert names.
        expected = [g.name for g in pg.generators]
        assert expected == [
            "weather_event_generator",
            "weather_npc_dialogue_generator",
            "weather_buff_generator",
            "weather_mail_generator",
            "weather_content_json_generator",
        ], (
            f"Pack's get_generators('weather_event') order = {expected}, "
            f"expected the 5-generator Event→Dialogue→Buff→Mail→ContentJson "
            f"order so each generator can consume the prior's output"
        )


class TestWeatherContentJsonGeneratorDeterministic:
    """WeatherContentJsonGenerator is the deterministic rollup — it
    consumes the 4 LLM-driven generators' outputs and emits a
    content.json with EditData blocks. Test it with a fully-populated
    prior_outputs envelope to verify the contract."""

    def _build_prior_outputs(self) -> dict:
        """Construct a complete prior_outputs dict matching the contract
        WeatherContentJsonGenerator reads (see _extract_*_from_prior
        helpers in the source)."""
        return {
            "manifest_generator": GeneratorOutput(
                files={
                    "manifest.json": {
                        "UniqueID": "TestWeatherMod",
                        "Name": "Test Weather Mod",
                    }
                }
            ),
            "weather_event_generator": GeneratorOutput(
                files={
                    "assets/data/weather_events.json": {
                        "events": [
                            {
                                "EventName": "Rainy_Bonus",
                                "WeatherCondition": "rainy",
                                "Season": None,
                                "Description": "Crops grow faster on rainy days.",
                                "Effects": [
                                    {"Stat": "Farming", "Value": 1, "Duration": 300}
                                ],
                            },
                            {
                                "EventName": "Stormy_Mining",
                                "WeatherCondition": "stormy",
                                "Season": None,
                                "Description": "Lightning powers the mines.",
                                "Effects": [
                                    {"Stat": "Mining", "Value": 2, "Duration": 300}
                                ],
                            },
                        ]
                    }
                }
            ),
            "weather_npc_dialogue_generator": GeneratorOutput(
                files={
                    "assets/data/weather_dialogue.json": {
                        "rainy_abigail": "I love the rain, @.",
                        "sunny_sebastian": "Too bright.",
                    }
                }
            ),
            "weather_buff_generator": GeneratorOutput(
                files={
                    "assets/data/weather_buffs.json": {
                        "buffs": [
                            {
                                "BuffName": "Rainy_Fishing",
                                "WeatherCondition": "rainy",
                                "Stat": "Fishing",
                                "Value": 2,
                                "Duration": 300,
                            }
                        ]
                    }
                }
            ),
            "weather_mail_generator": GeneratorOutput(
                files={
                    "mail/weather_announcement.json": {
                        "weather_announcement": "Dear @, big storm tomorrow! - Gunther"
                    }
                }
            ),
        }

    def test_emits_content_json_with_4_change_blocks(self) -> None:
        gen = WeatherContentJsonGenerator()
        prior = self._build_prior_outputs()
        inp: GeneratorInput = {
            "prompt": "add a rain storm event",
            "hint": {}, "t2_feedback": "",
            "request_id": "req_test_weather_1",
            "game": "stardew_valley",
            "prior_outputs": prior,
        }
        out = asyncio.run(gen.generate(inp))
        # The generator must have produced a content.json file.
        assert "content.json" in out.files, (
            f"WeatherContentJsonGenerator must emit content.json, "
            f"got files = {list(out.files.keys())}"
        )
        content = out.files["content.json"]
        # 2 events + 2 dialogue lines + 1 buff + 1 mail = 6 changes.
        # Wait — the dialogue test produced 2 entries but the test's
        # prior has 2 dialogue lines. The events test has 2 events.
        # So total = 2 + 2 + 1 + 1 = 6 changes.
        assert isinstance(content, dict)
        assert "Changes" in content
        assert isinstance(content["Changes"], list)
        assert len(content["Changes"]) == 6, (
            f"Expected 6 changes (2 events + 2 dialogue + 1 buff + 1 mail), "
            f"got {len(content['Changes'])}"
        )
        # Verify the mod_id propagated from manifest.
        assert out.metadata.get("mod_id") == "testweathermod", (
            f"WeatherContentJsonGenerator should lowercase the manifest's "
            f"UniqueID, got {out.metadata.get('mod_id')!r}"
        )

    def test_validates_missing_content_json(self) -> None:
        gen = WeatherContentJsonGenerator()
        empty_out = GeneratorOutput()
        errors = gen.validate_output(empty_out)
        assert any("content.json" in e for e in errors), (
            f"validate_output should flag missing content.json, "
            f"got errors = {errors}"
        )

    def test_validates_changes_key_missing(self) -> None:
        gen = WeatherContentJsonGenerator()
        out = GeneratorOutput()
        out.add_file("content.json", {"Format": "1.29.0"})  # no "Changes"
        errors = gen.validate_output(out)
        assert any("Changes" in e for e in errors), (
            f"validate_output should flag missing Changes key, "
            f"got errors = {errors}"
        )


class TestWeatherEventGeneratorFallback:
    """The LLM-driven generators (WeatherEventGenerator, etc.) fall
    back to a hardcoded payload when the LLM call fails. Verify the
    fallback paths produce valid output without ever calling the LLM.

    These tests patch generate_structured to raise — the generator
    must catch the exception, log the failure, and emit the fallback
    payload. The fallback payloads are real, shippable Content
    Patcher content (verified by the original discord-ops-hardening
    branch's integration tests)."""

    def test_weather_event_generator_fallback_emits_3_events(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherEventGenerator()
            inp: GeneratorInput = {
                "prompt": "add a rain storm event",
                "hint": {}, "t2_feedback": "",
                "request_id": "req_test_weather_fallback",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "assets/data/weather_events.json" in out.files
        events = out.files["assets/data/weather_events.json"]["events"]
        assert len(events) == 3, f"Fallback should emit 3 events, got {len(events)}"
        # Fallback includes 3 real Content Patcher event names.
        names = {e["EventName"] for e in events}
        assert "Rainy_Day_Bonus" in names
        assert "Stormy_Mining" in names
        assert "Snowy_Foraging" in names

    def test_weather_npc_dialogue_generator_fallback_emits_4_lines(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherNPCDialogueGenerator()
            inp: GeneratorInput = {
                "prompt": "weather dialogue",
                "hint": {}, "t2_feedback": "",
                "request_id": "req_test_dialogue_fb",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "assets/data/weather_dialogue.json" in out.files
        dialogue = out.files["assets/data/weather_dialogue.json"]
        assert len(dialogue) == 4, f"Fallback should emit 4 dialogue lines, got {len(dialogue)}"

    def test_weather_buff_generator_fallback_emits_3_buffs(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherBuffGenerator()
            inp: GeneratorInput = {
                "prompt": "weather buffs",
                "hint": {}, "t2_feedback": "",
                "request_id": "req_test_buff_fb",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "assets/data/weather_buffs.json" in out.files
        buffs = out.files["assets/data/weather_buffs.json"]["buffs"]
        assert len(buffs) == 3, f"Fallback should emit 3 buffs, got {len(buffs)}"

    def test_weather_mail_generator_fallback_emits_1_mail(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherMailGenerator()
            inp: GeneratorInput = {
                "prompt": "weather mail",
                "hint": {}, "t2_feedback": "",
                "request_id": "req_test_mail_fb",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        # Fallback emits ONE mail file under mail/...
        mail_files = [k for k in out.files if k.startswith("mail/")]
        assert len(mail_files) == 1, (
            f"Fallback should emit 1 mail file, got {mail_files}"
        )


class TestRouterWeatherEventPhase:
    """The router already has a weather_event priority override (see
    orchestrator/router.py:148-151). After this port, the phase is
    also registered in the pack AND has a router fallback arm.
    Verify the round-trip: prompt → route → pack.get_generators."""

    def test_weather_event_phase_is_registered(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack
        assert "weather_event" in StardewValleyPack.list_phases(), (
            "StardewValleyPack.supported_phases must include "
            "'weather_event' after the v88 port"
        )

    def test_router_fallback_for_weather_event(self) -> None:
        # Direct test of _default_generators_for_phase — this is the
        # defence-in-depth path that fires when the pack lookup fails.
        # Without this arm, the v22 WARNING would fire for every
        # weather_event routing.
        from orchestrator.router import _default_generators_for_phase
        result = _default_generators_for_phase("weather_event")
        assert result == [
            "weather_event_generator",
            "weather_npc_dialogue_generator",
            "weather_buff_generator",
            "weather_mail_generator",
            "weather_content_json_generator",
        ], (
            f"_default_generators_for_phase('weather_event') should "
            f"return the 5-generator list, got {result}"
        )

    def test_weather_event_priority_override_still_routes_correctly(self) -> None:
        # The v27 router priority override — "add a rain storm event"
        # → weather_event — must still work after the port (the port
        # didn't touch the override logic, just registered the phase).
        from orchestrator.router import route
        phase, hint = route("add a rain storm event for sunny days")
        assert phase == "weather_event", (
            f"v27 priority override should route 'add a rain storm "
            f"event' → weather_event, got {phase!r}"
        )
