"""Tests for the WeatherManifestGenerator (v101).

The manifest.json generator fixes the T2 TechnicalComplianceJudge
critical failure ("missing manifest.json is a critical issue preventing
the mod from loading") flagged in the 2026-07-09 audit of
req_08628445042f. The generator must:

- Always emit manifest.json (LLM or fallback path)
- Always include Format, UniqueID, Name, Version (Content Patcher
  required fields)
- Use snake_case UniqueID prefixed with "ai_generator."
- Validate that the manifest has the 4 required fields
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.weather_event import (
    WeatherManifestGenerator,
    WeatherManifestOutput,
)


class TestWeatherManifestGeneratorLLMSuccessPath:
    """The LLM call succeeds — manifest has the LLM-generated fields."""

    def test_emits_manifest_with_llm_fields(self) -> None:
        fake_response = {
            "UniqueID": "rainy_weather_events",
            "Name": "Rainy Weather Events",
            "Description": "Adds 5 weather events with rain-themed buffs.",
            "Author": "AI Generator",
            "Version": "1.0.0",
        }
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeatherManifestGenerator()
            inp: GeneratorInput = {
                "prompt": "add a weather event for rainy days",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_manifest_1",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "manifest.json" in out.files
        m = out.files["manifest.json"]
        assert isinstance(m, dict)
        assert m["Format"] == "1.29.0"
        assert m["UniqueID"] == "ai_generator.rainy_weather_events"
        assert m["Name"] == "Rainy Weather Events"
        assert m["Description"] == "Adds 5 weather events with rain-themed buffs."
        assert m["Version"] == "1.0.0"

    def test_unique_id_is_slugified_when_llm_includes_spaces(self) -> None:
        # LLMs sometimes include spaces in UniqueID despite the prompt.
        # The generator must slugify defensively.
        fake_response = {
            "UniqueID": "Rainy Weather Events Mod",
            "Name": "Rainy Weather",
            "Description": "...",
            "Author": "AI Generator",
            "Version": "1.0.0",
        }
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeatherManifestGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_manifest_2",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        m = out.files["manifest.json"]
        assert m["UniqueID"] == "ai_generator.rainy_weather_events_mod"

    def test_manifest_includes_content_patcher_dependencies(self) -> None:
        # The Content Patcher dependency is required so SMAPI loads
        # the mod as a content pack, not as a standalone mod.
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(return_value={
                "UniqueID": "test",
                "Name": "Test",
                "Description": "...",
                "Author": "AI Generator",
                "Version": "1.0.0",
            }),
        ):
            gen = WeatherManifestGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_manifest_3",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        m = out.files["manifest.json"]
        assert "ContentPackFor" in m
        assert m["ContentPackFor"]["UniqueID"] == "Pathoschild.ContentPatcher"
        assert m["ContentPackFor"]["MinimumVersion"] == "2.4.0"
        assert m["Dependencies"][0]["UniqueID"] == "Pathoschild.ContentPatcher"


class TestWeatherManifestGeneratorFallbackPath:
    """The LLM call fails — fallback path emits a valid manifest."""

    def test_emits_manifest_with_fallback_fields(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherManifestGenerator()
            inp: GeneratorInput = {
                "prompt": "add a weather event for rainy days",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_manifest_fallback",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "manifest.json" in out.files
        m = out.files["manifest.json"]
        assert m["Format"] == "1.29.0"
        # UniqueID is derived from the prompt via _slugify_mod_id
        assert m["UniqueID"].startswith("ai_generator.")
        # Name and Description are hardcoded fallback values
        assert m["Name"] == "Weather Events"
        assert "weather-based events" in m["Description"].lower()
        assert m["Version"] == "1.0.0"

    def test_metadata_mod_id_matches_manifest(self) -> None:
        # The metadata["mod_id"] is used by WeatherContentJsonGenerator
        # as the lowercased mod_id. It must match the manifest's UniqueID
        # (lowercased) so downstream code can correlate the two.
        with patch(
            "generators.packs.stardew_valley.features.weather_event.generate_structured",
            new=AsyncMock(side_effect=RuntimeError("simulated LLM failure")),
        ):
            gen = WeatherManifestGenerator()
            inp: GeneratorInput = {
                "prompt": "rainy weather",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_manifest_metadata",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        manifest_uid = out.files["manifest.json"]["UniqueID"]
        assert out.metadata["mod_id"] == manifest_uid.lower()


class TestWeatherManifestGeneratorValidation:
    """validate_output catches the 4 required-fields invariant."""

    def test_flags_missing_manifest(self) -> None:
        gen = WeatherManifestGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("manifest.json missing" in e for e in errors)

    def test_flags_missing_required_field(self) -> None:
        gen = WeatherManifestGenerator()
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0", "UniqueID": "x"})
        errors = gen.validate_output(out)
        # Should flag missing Name + Version
        assert any("Name" in e for e in errors)
        assert any("Version" in e for e in errors)

    def test_passes_with_all_required_fields(self) -> None:
        gen = WeatherManifestGenerator()
        out = GeneratorOutput()
        out.add_file("manifest.json", {
            "Format": "1.29.0",
            "UniqueID": "ai_generator.x",
            "Name": "X",
            "Version": "1.0.0",
        })
        errors = gen.validate_output(out)
        assert errors == []


class TestManifestGeneratorRunsFirst:
    """The pack's execution_order must put the manifest generator first."""

    def test_manifest_is_first_in_execution_order(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack
        pg = StardewValleyPack.get_generators("weather_event")
        assert pg.execution_order[0] == "weather_manifest_generator", (
            "weather_manifest_generator must be first in execution_order "
            "so the manifest.json exists before downstream generators "
            "consume prior_outputs for the manifest slot; got "
            f"{pg.execution_order}"
        )
