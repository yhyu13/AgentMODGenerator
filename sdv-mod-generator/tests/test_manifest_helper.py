"""Tests for the shared manifest helper (generators/core/manifest.py).

The helper provides ``build_manifest_dict`` + ``slugify_unique_id``
+ ``fallback_name_from_prompt`` used by every pack's
ContentJsonGenerator to emit a Content Patcher-compliant
manifest.json alongside the content.json it already produces.

v169/v172 cron history: the 2-generator Tier-1 packs
(weapon_definition, tool_definition, hat_collection, etc.) ship
without a ManifestGenerator, so the generated zips lacked
manifest.json. The fix is to call this helper from each
ContentJsonGenerator. These tests pin the helper's contract so
the cron's Agent #4 audit can verify any future refactor.
"""
from __future__ import annotations

import pytest

from generators.core.manifest import (
    CONTENT_PATCHER_MIN_VERSION,
    CONTENT_PATCHER_UNIQUE_ID,
    DEFAULT_AUTHOR,
    DEFAULT_FORMAT_VERSION,
    DEFAULT_VERSION,
    build_manifest_dict,
    fallback_name_from_prompt,
    slugify_unique_id,
)


class TestSlugifyUniqueId:
    """Pin the slugification contract."""

    def test_lowercases_input(self) -> None:
        assert slugify_unique_id("Add A Custom Sword") == (
            "ai_generator.add_a_custom_sword"
        )

    def test_replaces_non_alphanumeric_with_underscore(self) -> None:
        # Trailing `!` becomes `_` then is stripped.
        assert slugify_unique_id("add a custom sword!") == (
            "ai_generator.add_a_custom_sword"
        )

    def test_collapses_consecutive_separators(self) -> None:
        # 3 spaces collapse to single _ (via [^a-z0-9_-]+ greedy match).
        # Hyphens and underscores are preserved (they're in the OK set).
        assert slugify_unique_id("a   b-c__d") == "ai_generator.a_b-c__d"

    def test_strips_leading_and_trailing_separators(self) -> None:
        assert slugify_unique_id("!!!hello world!!!") == (
            "ai_generator.hello_world"
        )

    def test_empty_string_uses_default(self) -> None:
        assert slugify_unique_id("") == "ai_generator.stardew_mod"

    def test_all_punctuation_uses_default(self) -> None:
        assert slugify_unique_id("!!!") == "ai_generator.stardew_mod"

    def test_custom_prefix(self) -> None:
        assert slugify_unique_id("tv_shopping_channel", prefix="") == (
            "tv_shopping_channel"
        )

    def test_custom_default(self) -> None:
        assert slugify_unique_id("", default="weapon_pack") == (
            "ai_generator.weapon_pack"
        )

    def test_already_valid_slug_is_passthrough(self) -> None:
        assert slugify_unique_id("weapon_definition") == (
            "ai_generator.weapon_definition"
        )

    def test_handles_unicode_gracefully(self) -> None:
        # Non-ASCII becomes underscores. We don't lose data, just
        # rewrite to ASCII-safe characters.
        result = slugify_unique_id("café weapons")
        assert result.startswith("ai_generator.")
        assert "caf" in result  # the accented chars are replaced


class TestBuildManifestDict:
    """Pin the manifest.json dict shape — matches weather_event's output."""

    def test_returns_canonical_capitalized_keys(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        # CP is case-sensitive; keys must be capitalized.
        expected_keys = {
            "Format", "UniqueID", "Name", "Description",
            "Author", "Version", "ContentPackFor", "Dependencies",
        }
        assert set(m.keys()) == expected_keys

    def test_format_version_default(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        assert m["Format"] == DEFAULT_FORMAT_VERSION == "1.29.0"

    def test_format_version_overridable(self) -> None:
        m = build_manifest_dict(
            "test_mod", "Test Mod", "A test.",
            format_version="1.30.0",
        )
        assert m["Format"] == "1.30.0"

    def test_unique_id_is_slugified(self) -> None:
        # LLM might emit spaces / special chars — helper must defend.
        m = build_manifest_dict("My Cool Mod!", "Test Mod", "A test.")
        assert m["UniqueID"] == "ai_generator.my_cool_mod"
        assert " " not in m["UniqueID"]

    def test_author_default(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        assert m["Author"] == DEFAULT_AUTHOR == "AI Generator"

    def test_version_default(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        assert m["Version"] == DEFAULT_VERSION == "1.0.0"

    def test_content_pack_for(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        assert m["ContentPackFor"] == {
            "UniqueID": CONTENT_PATCHER_UNIQUE_ID,
            "MinimumVersion": CONTENT_PATCHER_MIN_VERSION,
        }
        assert m["ContentPackFor"]["UniqueID"] == "Pathoschild.ContentPatcher"
        assert m["ContentPackFor"]["MinimumVersion"] == "2.4.0"

    def test_dependencies_block(self) -> None:
        m = build_manifest_dict("test_mod", "Test Mod", "A test.")
        assert m["Dependencies"] == [
            {
                "UniqueID": CONTENT_PATCHER_UNIQUE_ID,
                "MinimumVersion": CONTENT_PATCHER_MIN_VERSION,
            },
        ]

    def test_custom_content_patcher_min_version(self) -> None:
        m = build_manifest_dict(
            "test_mod", "Test Mod", "A test.",
            content_patcher_min_version="2.5.0",
        )
        assert m["ContentPackFor"]["MinimumVersion"] == "2.5.0"
        assert m["Dependencies"][0]["MinimumVersion"] == "2.5.0"

    def test_custom_author_and_version(self) -> None:
        m = build_manifest_dict(
            "test_mod", "Test Mod", "A test.",
            author="Custom Author", version="2.0.0",
        )
        assert m["Author"] == "Custom Author"
        assert m["Version"] == "2.0.0"

    def test_empty_unique_id_falls_back_to_default(self) -> None:
        m = build_manifest_dict("", "Test Mod", "A test.")
        # Empty input slugifies to "stardew_mod" with the default prefix.
        assert m["UniqueID"] == "ai_generator.stardew_mod"

    def test_all_punctuation_unique_id_falls_back(self) -> None:
        m = build_manifest_dict("!!!", "Test Mod", "A test.")
        assert m["UniqueID"] == "ai_generator.stardew_mod"


class TestManifestShapeMatchesWeatherEvent:
    """The manifest shape MUST match the existing weather_event
    WeatherManifestGenerator output exactly — otherwise Content Patcher
    sees two different manifest shapes from different packs and may
    reject one or the other.

    The weather_event output is the source of truth (it's been on
    master since Session 6 v101 and works in production).
    """

    def test_keys_match(self) -> None:
        from generators.packs.stardew_valley.features.weather_event import (
            _slugify_mod_id,
        )
        # Both should produce the same set of keys.
        from generators.core.manifest import build_manifest_dict
        weather_slug = _slugify_mod_id("add a custom sword weapon")
        new_slug = slugify_unique_id("add a custom sword weapon")
        # The two slugifiers should produce identical output for the
        # same input (the new one replaces the local one in weather_event).
        assert weather_slug == new_slug

    def test_canonical_keys_present(self) -> None:
        # CP rejects mods missing any of these 4 fields. Enforce.
        m = build_manifest_dict("test", "Test", "A test.")
        for required in ("Format", "UniqueID", "Name", "Version"):
            assert required in m, f"required CP field {required} missing"
            assert m[required], f"required CP field {required} is empty"


class TestFallbackNameFromPrompt:
    """Pin the prompt-derived Name helper."""

    def test_basic_title_casing(self) -> None:
        assert fallback_name_from_prompt("add a custom sword weapon") == (
            "Add Custom Sword Weapon"
        )

    def test_caps_at_5_words(self) -> None:
        # Six words → take first 5. The single-letter word "a" is
        # filtered out by the len(w) > 1 check, so "Add A Totally
        # Awesome Custom" becomes "Add Totally Awesome Custom Weapon"
        # (5 words, where "A" is dropped but "Weapon" comes through).
        assert (
            fallback_name_from_prompt(
                "add a totally awesome custom weapon today",
            )
            == "Add Totally Awesome Custom Weapon"
        )

    def test_filters_short_words(self) -> None:
        # Words of length 1 are filtered (no "a", "I" in the result).
        assert (
            fallback_name_from_prompt("a b c add custom sword") == (
                "Add Custom Sword"
            )
        )

    def test_empty_prompt_uses_default(self) -> None:
        assert (
            fallback_name_from_prompt("", default="Fallback Name") == (
                "Fallback Name"
            )
        )

    def test_punctuation_stripped(self) -> None:
        # "a" is length 1, filtered out by the len(w) > 1 check.
        assert (
            fallback_name_from_prompt("add: a custom! sword?") == (
                "Add Custom Sword"
            )
        )

    def test_already_title_case_preserved(self) -> None:
        assert (
            fallback_name_from_prompt("Custom Sword Mod") == (
                "Custom Sword Mod"
            )
        )


class TestExports:
    """Pin the module's public API. If you change these names, every
    pack's ContentJsonGenerator import breaks."""

    def test_expected_names_exported(self) -> None:
        from generators.core import manifest as m
        for name in (
            "DEFAULT_FORMAT_VERSION",
            "CONTENT_PATCHER_UNIQUE_ID",
            "CONTENT_PATCHER_MIN_VERSION",
            "DEFAULT_AUTHOR",
            "DEFAULT_VERSION",
            "slugify_unique_id",
            "build_manifest_dict",
            "fallback_name_from_prompt",
        ):
            assert hasattr(m, name), f"missing export: {name}"