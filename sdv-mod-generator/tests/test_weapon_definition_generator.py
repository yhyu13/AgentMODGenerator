"""Tests for the weapon_definition pack (DefinitionGenerator + ContentJsonGenerator).

v169 round landed the first cooperating generator of the two-generator
``weapon_definition`` pack: the ``WeaponDefinitionDefinitionGenerator``
(LLM-driven row producer with sanitizers, Pydantic schemas, and a
2-weapon deterministic fallback). v170 round appended the
``WeaponDefinitionContentJsonGenerator`` (deterministic content.json
assembler that reads the DefinitionGenerator's ``prior_outputs`` and
emits the ``Data/Weapons`` + ``Strings/UI`` EditData changes).
v171 round extends this test file with the ContentJsonGenerator test
classes (5 new test classes appended after ``TestWeaponDefinitionRoundTrip``).

Mirrors the v88 ``tests/test_weather_event_generator.py`` and v101
``tests/test_weather_manifest_generator.py`` recipes:

- Hermetic — does not import app.config, does not talk to
  Postgres/Redis/LLM. Runs in < 100ms.
- LLM success path: patch ``generate_structured`` to return a
  crafted response, verify the generator emits it.
- LLM fallback path: patch ``generate_structured`` to raise,
  verify the 2-weapon hardcoded fallback fires.
- ``validate_output`` contract pinning: required keys, duplicate
  ItemId detection, ``custom_weapon_`` prefix detection, content.json
  shape (Format + 2 Changes, every row key prefixed ``custom_weapon_``).
- Sanitizer hardening: damage clamp, type enum snap,
  ``_sanitize_texture`` path-traversal guard rejects ``..``
  segments. The ContentJsonGenerator exercises these sanitizers
  indirectly via its row-construction path (MaxDamage clamp,
  empty DisplayName default, path-traversal texture clamp).
- Round-trip integration test: ``generate()`` then
  ``validate_output()`` must always pass.

The pack file is at
``generators/packs/stardew_valley/features/weapon_definition/__init__.py``.
The deterministic fallback is the 2 curated weapons (Wood Sword,
Iron Dagger) defined in ``_fallback_weapon_list``.
"""
from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.weapon_definition import (
    WeaponDefinitionDefinitionGenerator,
    WeaponDefinitionContentJsonGenerator,
    WeaponListSchema,
    WeaponSchema,
    _MIN_WEAPONS,
    _MAX_WEAPONS,
    _DEFAULT_WEAPONS,
    _WEAPON_TOKEN_PREFIX,
    _MIN_DAMAGE,
    _MAX_DAMAGE,
    _MIN_CRIT_CHANCE,
    _MAX_CRIT_CHANCE,
    _DEFAULT_CRIT_CHANCE,
    _MIN_CRIT_MULTIPLIER,
    _MAX_CRIT_MULTIPLIER,
    _DEFAULT_CRIT_MULTIPLIER,
    _MIN_SPEED,
    _MAX_SPEED,
    _DEFAULT_SPEED,
    _WEAPON_TYPES,
    _DEFAULT_WEAPON_TYPE,
    _DEFAULT_MOD_ID,
    _DEFAULT_TEXTURE_TEMPLATE,
    _NAME_KEY_TEMPLATE,
    _DESCRIPTION_KEY_TEMPLATE,
    _FORMAT_VERSION,
    _sanitize_weapon_token,
    _sanitize_damage,
    _sanitize_crit_chance,
    _sanitize_crit_multiplier,
    _sanitize_speed,
    _sanitize_weapon_type,
    _sanitize_display_name,
    _sanitize_description,
    _sanitize_texture,
    _sanitize_count,
    _sanitize_weapon_row,
    _fallback_weapon,
    _fallback_weapon_list,
)


# ---------------------------------------------------------------------
# Phase registration / generator class basics
# ---------------------------------------------------------------------


class TestWeaponDefinitionGeneratorBasics:
    """The DefinitionGenerator class identity and pack registration."""

    def test_class_identity(self) -> None:
        # The class must declare its name/phase/game metadata so the
        # pack's PhaseGenerators registry can look it up.
        assert (
            WeaponDefinitionDefinitionGenerator.name
            == "weapon_definition_definition_generator"
        )
        assert (
            WeaponDefinitionDefinitionGenerator.phase
            == "weapon_definition"
        )
        assert (
            WeaponDefinitionDefinitionGenerator.game
            == "stardew_valley"
        )

    def test_weapon_definition_phase_is_registered(self) -> None:
        # The phase must appear in the pack's supported_phases list
        # for the orchestrator to route prompts to it.
        from generators.packs.stardew_valley import StardewValleyPack

        assert "weapon_definition" in StardewValleyPack.list_phases(), (
            "StardewValleyPack.supported_phases must include "
            "'weapon_definition' after the v169 port"
        )

    def test_get_generators_returns_both_generators(self) -> None:
        # v170 — both generators are registered. The
        # DefinitionGenerator runs first and emits
        # assets/weapon_definition/weapons.json; the
        # ContentJsonGenerator runs second and reads that prior
        # output to emit content.json. This test pins the v170
        # final both-generators-registered state.
        from generators.packs.stardew_valley import StardewValleyPack

        pg = StardewValleyPack.get_generators("weapon_definition")
        names = [g.name for g in pg.generators]
        assert names == [
            "weapon_definition_definition_generator",
            "weapon_definition_content_json_generator",
        ], (
            f"v170 must register BOTH generators (definition first, "
            f"content_json second); got {names}"
        )
        assert pg.execution_order == [
            "weapon_definition_definition_generator",
            "weapon_definition_content_json_generator",
        ], (
            f"v170 execution_order must be a list of both "
            f"generator names in dependency order, got "
            f"{list(pg.execution_order)}"
        )


# ---------------------------------------------------------------------
# LLM success path
# ---------------------------------------------------------------------


class TestWeaponDefinitionGeneratorLLMSuccess:
    """The LLM call succeeds — generator emits the LLM-provided rows.

    Mirrors ``TestWeatherManifestGeneratorLLMSuccessPath`` from
    ``tests/test_weather_manifest_generator.py``.
    """

    def test_emits_weapons_json_with_llm_provided_rows(self) -> None:
        fake_response = {
            "weapons": [
                {
                    "ItemId": "custom_weapon_bone_axe",
                    "Name": "Bone Axe",
                    "Description": "A heavy axe carved from prehistoric bone.",
                    "MinDamage": 30,
                    "MaxDamage": 60,
                    "CritChance": 0.04,
                    "CritMultiplier": 2.5,
                    "Speed": 4,
                    "Type": "Club",
                    "Texture": "Weapons/bone_axe",
                    "DisplayName": "Weapon.custom_weapon_bone_axe.Name",
                },
                {
                    "ItemId": "custom_weapon_fire_spear",
                    "Name": "Fire Spear",
                    "Description": "A spear wreathed in eternal flame.",
                    "MinDamage": 20,
                    "MaxDamage": 50,
                    "CritChance": 0.10,
                    "CritMultiplier": 3.5,
                    "Speed": 2,
                    "Type": "Sword",
                    "Texture": "Weapons/fire_spear",
                    "DisplayName": "Weapon.custom_weapon_fire_spear.Name",
                },
            ]
        }
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "add a bone axe and a fire spear",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_llm_success",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert (
            "assets/weapon_definition/weapons.json" in out.files
        ), (
            "DefinitionGenerator must emit "
            "assets/weapon_definition/weapons.json, got files = "
            f"{list(out.files.keys())}"
        )
        data = out.files["assets/weapon_definition/weapons.json"]
        assert isinstance(data, dict)
        weapons = data["weapons"]
        assert len(weapons) == 2, (
            f"LLM-success path must emit exactly the 2 rows the "
            f"LLM returned, got {len(weapons)}"
        )
        # Tokens get sanitized via _sanitize_weapon_token (already
        # prefixed in the input, so they round-trip unchanged).
        assert weapons[0]["ItemId"] == "custom_weapon_bone_axe"
        assert weapons[1]["ItemId"] == "custom_weapon_fire_spear"
        # metadata['weapon_count'] matches the row count.
        assert out.metadata["weapon_count"] == 2
        assert out.metadata["weapon_ids"] == [
            "custom_weapon_bone_axe",
            "custom_weapon_fire_spear",
        ]

    def test_caps_weapon_list_at_max(self) -> None:
        # _MAX_WEAPONS is 4; an LLM that returns 6 rows must be capped
        # to 4 (the per-pack envelope).
        fake_response = {
            "weapons": [
                {
                    "ItemId": f"custom_weapon_weapon_{i}",
                    "Name": f"Weapon {i}",
                    "Description": f"Description {i}",
                    "MinDamage": 10,
                    "MaxDamage": 20,
                    "CritChance": 0.05,
                    "CritMultiplier": 2.0,
                    "Speed": 0,
                    "Type": "Sword",
                    "Texture": f"Weapons/weapon_{i}",
                    "DisplayName": (
                        f"Weapon.custom_weapon_weapon_{i}.Name"
                    ),
                }
                for i in range(6)
            ]
        }
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_cap",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        weapons = out.files[
            "assets/weapon_definition/weapons.json"
        ]["weapons"]
        assert len(weapons) == _MAX_WEAPONS, (
            f"LLM list of 6 must be capped to _MAX_WEAPONS "
            f"({_MAX_WEAPONS}), got {len(weapons)}"
        )


# ---------------------------------------------------------------------
# LLM fallback path — the safety net for file-only mode
# ---------------------------------------------------------------------


class TestWeaponDefinitionGeneratorFallback:
    """The LLM call fails — fallback path emits 2 curated weapons.

    Mirrors ``TestWeatherEventGeneratorFallback`` and
    ``TestWeatherManifestGeneratorFallbackPath``.
    """

    def test_emits_2_fallback_weapons(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(
                side_effect=RuntimeError("simulated LLM failure")
            ),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "add a custom sword",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_fallback",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        assert "assets/weapon_definition/weapons.json" in out.files
        weapons = out.files[
            "assets/weapon_definition/weapons.json"
        ]["weapons"]
        assert len(weapons) == 2, (
            f"Fallback should emit exactly 2 curated weapons "
            f"(Wood Sword, Iron Dagger), got {len(weapons)}"
        )
        ids = {w["ItemId"] for w in weapons}
        assert "custom_weapon_wood_sword" in ids
        assert "custom_weapon_iron_dagger" in ids
        # The fallback path must populate metadata for Agent #4.
        assert out.metadata["weapon_count"] == 2
        assert out.metadata["weapon_ids"] == [
            "custom_weapon_wood_sword",
            "custom_weapon_iron_dagger",
        ]

    def test_fallback_fires_on_validation_error(self) -> None:
        # The generate() method catches (ValueError, RuntimeError,
        # IOError, ValidationError) and emits the fallback. Verify
        # the ValidationError branch specifically (it's the most
        # likely failure mode — Pydantic rejecting bad LLM JSON).
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"weapons": "not a list"}),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_validation_err",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        weapons = out.files[
            "assets/weapon_definition/weapons.json"
        ]["weapons"]
        assert len(weapons) == _DEFAULT_WEAPONS, (
            f"ValidationError path must fall back to "
            f"{_DEFAULT_WEAPONS} curated weapons, got {len(weapons)}"
        )

    def test_fallback_weapons_have_required_shape(self) -> None:
        # Each fallback weapon row must carry all 10 fields per the
        # WeaponSchema. This is the per-row contract pinned by
        # validate_output.
        required_fields = (
            "ItemId",
            "Name",
            "Description",
            "MinDamage",
            "MaxDamage",
            "CritChance",
            "CritMultiplier",
            "Speed",
            "Type",
            "Texture",
            "DisplayName",
        )
        for weapon in _fallback_weapon_list():
            for field in required_fields:
                assert field in weapon, (
                    f"Fallback weapon missing required field {field!r}: "
                    f"{weapon}"
                )

    def test_fallback_envelopes_are_in_range(self) -> None:
        # Fallback rows must satisfy all envelopes so the per-row
        # clamps never need to fire for the deterministic path.
        for weapon in _fallback_weapon_list():
            assert _MIN_DAMAGE <= weapon["MinDamage"] <= _MAX_DAMAGE
            assert _MIN_DAMAGE <= weapon["MaxDamage"] <= _MAX_DAMAGE
            assert weapon["MaxDamage"] >= weapon["MinDamage"]
            assert (
                _MIN_CRIT_CHANCE
                <= weapon["CritChance"]
                <= _MAX_CRIT_CHANCE
            )
            assert (
                _MIN_CRIT_MULTIPLIER
                <= weapon["CritMultiplier"]
                <= _MAX_CRIT_MULTIPLIER
            )
            assert _MIN_SPEED <= weapon["Speed"] <= _MAX_SPEED
            assert weapon["Type"] in _WEAPON_TYPES


# ---------------------------------------------------------------------
# validate_output contract pinning
# ---------------------------------------------------------------------


class TestWeaponDefinitionGeneratorValidateOutput:
    """validate_output pins the per-row shape contract.

    Mirrors ``TestWeatherManifestGeneratorValidation`` — required
    keys, contract violations, clean-state pass.
    """

    def test_flags_missing_weapons_json(self) -> None:
        gen = WeaponDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any(
            "assets/weapon_definition/weapons.json missing" in e
            for e in errors
        ), (
            f"validate_output must flag missing weapons.json, "
            f"got errors = {errors}"
        )

    def test_flags_empty_weapons_list(self) -> None:
        gen = WeaponDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/weapon_definition/weapons.json",
            {"weapons": []},
        )
        errors = gen.validate_output(out)
        assert any(
            "weapons list missing or too short" in e
            for e in errors
        ), (
            f"validate_output must flag empty weapons list, "
            f"got errors = {errors}"
        )

    def test_flags_missing_required_field(self) -> None:
        # A weapon row missing any of the 11 required fields must
        # be flagged. We omit 'CritMultiplier' to test the field-
        # missing case.
        gen = WeaponDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/weapon_definition/weapons.json",
            {
                "weapons": [
                    {
                        "ItemId": "custom_weapon_test",
                        "Name": "Test",
                        "Description": "Test weapon",
                        "MinDamage": 10,
                        "MaxDamage": 20,
                        "CritChance": 0.05,
                        # CritMultiplier intentionally omitted
                        "Speed": 0,
                        "Type": "Sword",
                        "Texture": "Weapons/test",
                        "DisplayName": "Weapon.custom_weapon_test.Name",
                    }
                ]
            },
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'CritMultiplier'" in e for e in errors
        ), (
            f"validate_output must flag missing CritMultiplier "
            f"field, got errors = {errors}"
        )

    def test_flags_duplicate_item_id_case_insensitive(self) -> None:
        # The validate_output dedup is case-insensitive (uses
        # seen_tokens.add(token.lower())) so two rows with
        # ItemIds differing only by case are duplicates.
        gen = WeaponDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/weapon_definition/weapons.json",
            {
                "weapons": [
                    _fallback_weapon(
                        item_id="custom_weapon_test",
                        name="Test",
                        description="Test",
                        min_damage=10,
                        max_damage=20,
                        crit_chance=0.05,
                        crit_multiplier=2.0,
                        speed=0,
                        weapon_type="Sword",
                    ),
                    _fallback_weapon(
                        item_id="custom_weapon_TEST",
                        name="Test 2",
                        description="Test 2",
                        min_damage=10,
                        max_damage=20,
                        crit_chance=0.05,
                        crit_multiplier=2.0,
                        speed=0,
                        weapon_type="Sword",
                    ),
                ]
            },
        )
        errors = gen.validate_output(out)
        assert any(
            "duplicate ItemId" in e for e in errors
        ), (
            f"validate_output must flag duplicate ItemId "
            f"(case-insensitive), got errors = {errors}"
        )

    def test_flags_missing_prefix(self) -> None:
        # Per the v169 review: every ItemId must be prefixed with
        # 'custom_weapon_'. A row missing the prefix must be
        # flagged.
        gen = WeaponDefinitionDefinitionGenerator()
        bad_row = _fallback_weapon(
            item_id="not_prefixed_weapon",
            name="Bad",
            description="Missing prefix",
            min_damage=10,
            max_damage=20,
            crit_chance=0.05,
            crit_multiplier=2.0,
            speed=0,
            weapon_type="Sword",
        )
        out = GeneratorOutput()
        out.add_file(
            "assets/weapon_definition/weapons.json",
            {"weapons": [bad_row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'custom_weapon_' prefix" in e
            for e in errors
        ), (
            f"validate_output must flag missing custom_weapon_ "
            f"prefix, got errors = {errors}"
        )

    def test_passes_with_valid_weapons(self) -> None:
        gen = WeaponDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/weapon_definition/weapons.json",
            {"weapons": _fallback_weapon_list()},
        )
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Fallback list (2 weapons, all 11 fields, all "
            f"prefixed) must produce no validation errors, got "
            f"errors = {errors}"
        )


# ---------------------------------------------------------------------
# Sanitizer hardening — unit tests for the 10 _sanitize_* helpers
# ---------------------------------------------------------------------


class TestSanitizers:
    """Unit tests for the per-field sanitizers.

    These run without instantiating the generator and without
    touching the LLM. Each test is a pure function check.

    The path-traversal guard test for ``_sanitize_texture`` is the
    defensive check that prevents the v168-style "malicious LLM
    smuggles ``../../../etc/passwd`` into the content.json" failure
    mode that the dispatcher SKILL.md called out as the top
    test-verifier concern.
    """

    def test_sanitize_weapon_token_adds_prefix(self) -> None:
        # Tokens missing the prefix get it added.
        assert (
            _sanitize_weapon_token("iron_dagger")
            == "custom_weapon_iron_dagger"
        )
        # Tokens with the prefix round-trip.
        assert (
            _sanitize_weapon_token("custom_weapon_wood_sword")
            == "custom_weapon_wood_sword"
        )
        # Leading-digit tokens get the prefix prepended (digits
        # are not valid SDV weapon keys).
        assert (
            _sanitize_weapon_token("2nd_age_blade")
            == "custom_weapon_2nd_age_blade"
        )

    def test_sanitize_weapon_token_fallback_for_none(self) -> None:
        # None input must produce a non-empty token (so downstream
        # content.json never sees an empty ItemId).
        assert (
            _sanitize_weapon_token(None)
            == f"{_WEAPON_TOKEN_PREFIX}_default"
        )

    def test_sanitize_damage_clamps_to_envelope(self) -> None:
        # Out-of-range high values clamp to _MAX_DAMAGE.
        assert _sanitize_damage(999) == _MAX_DAMAGE
        # Out-of-range low values clamp to _MIN_DAMAGE.
        assert _sanitize_damage(-5) == _MIN_DAMAGE
        # In-range values pass through.
        assert _sanitize_damage(50) == 50
        # Non-numeric inputs fall back to _MIN_DAMAGE (0).
        assert _sanitize_damage("not a number") == _MIN_DAMAGE

    def test_sanitize_crit_chance_clamps_to_unit_interval(self) -> None:
        assert _sanitize_crit_chance(2.5) == _MAX_CRIT_CHANCE
        assert _sanitize_crit_chance(-0.1) == _MIN_CRIT_CHANCE
        assert _sanitize_crit_chance(0.5) == 0.5

    def test_sanitize_crit_multiplier_clamps_to_1_to_5(self) -> None:
        assert _sanitize_crit_multiplier(10.0) == _MAX_CRIT_MULTIPLIER
        assert _sanitize_crit_multiplier(0.5) == _MIN_CRIT_MULTIPLIER
        assert _sanitize_crit_multiplier(2.5) == 2.5

    def test_sanitize_speed_clamps_to_0_to_15(self) -> None:
        assert _sanitize_speed(99) == _MAX_SPEED
        assert _sanitize_speed(-1) == _MIN_SPEED
        assert _sanitize_speed(5) == 5

    def test_sanitize_weapon_type_snaps_to_enum(self) -> None:
        # Case-insensitive matching.
        assert _sanitize_weapon_type("sword") == "Sword"
        assert _sanitize_weapon_type("DAGGER") == "Dagger"
        assert _sanitize_weapon_type("Club") == "Club"
        # Unknown values fall back to _DEFAULT_WEAPON_TYPE.
        assert _sanitize_weapon_type("Wand") == _DEFAULT_WEAPON_TYPE
        # Non-string input falls back too.
        assert _sanitize_weapon_type(42) == _DEFAULT_WEAPON_TYPE

    def test_sanitize_texture_rejects_path_traversal(self) -> None:
        # The v168 SKILL.md flagged this as the top test-verifier
        # concern: a malicious LLM response that smuggles
        # '..' path-traversal tokens into the content.json.
        # _sanitize_texture MUST collapse any '..' segment to the
        # default template.
        result = _sanitize_texture(
            "../../../etc/passwd",
            default_template="Weapons/{weapon_token}",
            weapon_token="custom_weapon_test",
        )
        assert result == "Weapons/custom_weapon_test", (
            f"path-traversal segments must collapse to the "
            f"default template, got {result!r}"
        )
        # Mid-path '..' also rejected (foo/../bar → default).
        result2 = _sanitize_texture(
            "Weapons/foo/../bar",
            default_template="Weapons/{weapon_token}",
            weapon_token="custom_weapon_test",
        )
        assert result2 == "Weapons/custom_weapon_test", (
            f"mid-path '..' must also collapse to default, "
            f"got {result2!r}"
        )

    def test_sanitize_texture_rejects_absolute_prefix(self) -> None:
        # Leading '/' is stripped, but the resulting path must not
        # be allowed to escape the mod's asset directory. The
        # pack's downstream consumer (content.json) handles the
        # 'relative-to-mod-root' semantics; we just need to
        # confirm the sanitizer does not pass through absolute
        # paths unchanged.
        result = _sanitize_texture(
            "/absolute/path",
            default_template="Weapons/{weapon_token}",
            weapon_token="custom_weapon_test",
        )
        # Leading slash gets stripped, the remainder is the
        # resulting relative path.
        assert not result.startswith("/"), (
            f"absolute-path texture must be normalized to "
            f"relative, got {result!r}"
        )

    def test_sanitize_texture_falls_back_for_invalid_input(self) -> None:
        # None / empty / non-string inputs collapse to the default.
        result = _sanitize_texture(
            None,
            default_template="Weapons/{weapon_token}",
            weapon_token="custom_weapon_test",
        )
        assert result == "Weapons/custom_weapon_test"
        result2 = _sanitize_texture(
            "",
            default_template="Weapons/{weapon_token}",
            weapon_token="custom_weapon_test",
        )
        assert result2 == "Weapons/custom_weapon_test"

    def test_sanitize_count_clamps_to_envelope(self) -> None:
        assert _sanitize_count(0) == _MIN_WEAPONS
        assert _sanitize_count(99) == _MAX_WEAPONS
        assert _sanitize_count(3) == 3
        # Non-numeric falls back to _DEFAULT_WEAPONS.
        assert _sanitize_count("not a number") == _DEFAULT_WEAPONS

    def test_sanitize_display_name_caps_at_32_chars(self) -> None:
        long_name = "A" * 100
        result = _sanitize_display_name(long_name)
        assert len(result) <= 32, (
            f"display name must be capped at 32 chars, got "
            f"{len(result)} chars: {result!r}"
        )
        # The truncation uses an ellipsis suffix (\u2026) per the
        # v92 custom_powers convention.
        assert result.endswith("\u2026"), (
            f"truncated display name must end with ellipsis "
            f"(\\u2026), got {result!r}"
        )

    def test_sanitize_description_caps_at_128_chars(self) -> None:
        long_desc = "B" * 200
        result = _sanitize_description(long_desc)
        assert len(result) <= 128, (
            f"description must be capped at 128 chars, got "
            f"{len(result)} chars"
        )
        assert result.endswith("\u2026"), (
            f"truncated description must end with ellipsis, "
            f"got {result!r}"
        )

    def test_sanitize_weapon_row_clamps_out_of_range_damage(self) -> None:
        # A row with MinDamage=999 (out of envelope) must be
        # clamped to _MAX_DAMAGE by _sanitize_weapon_row.
        row = {
            "ItemId": "custom_weapon_test",
            "Name": "Test",
            "Description": "Test",
            "MinDamage": 999,
            "MaxDamage": 1500,
            "CritChance": 5.0,
            "CritMultiplier": 10.0,
            "Speed": 99,
            "Type": "Wand",  # unknown → _DEFAULT_WEAPON_TYPE
            "Texture": "Weapons/test",
            "DisplayName": "",
        }
        sanitized = _sanitize_weapon_row(row)
        assert sanitized["MinDamage"] == _MAX_DAMAGE
        assert sanitized["MaxDamage"] == _MAX_DAMAGE
        assert sanitized["MaxDamage"] >= sanitized["MinDamage"]
        assert sanitized["CritChance"] == _MAX_CRIT_CHANCE
        assert sanitized["CritMultiplier"] == _MAX_CRIT_MULTIPLIER
        assert sanitized["Speed"] == _MAX_SPEED
        assert sanitized["Type"] == _DEFAULT_WEAPON_TYPE
        # Empty DisplayName gets defaulted to the lang-key template.
        assert sanitized["DisplayName"] == (
            "Weapon.custom_weapon_test.Name"
        )

    def test_sanitize_weapon_row_enforces_max_damage_ge_min(self) -> None:
        # If LLM emits MaxDamage < MinDamage, the helper must
        # clamp MaxDamage up to MinDamage (per the v169 plan
        # invariant).
        row = {
            "ItemId": "custom_weapon_test",
            "Name": "Test",
            "Description": "Test",
            "MinDamage": 50,
            "MaxDamage": 10,  # < MinDamage!
            "CritChance": 0.05,
            "CritMultiplier": 2.0,
            "Speed": 0,
            "Type": "Sword",
            "Texture": "Weapons/test",
            "DisplayName": "Weapon.custom_weapon_test.Name",
        }
        sanitized = _sanitize_weapon_row(row)
        assert sanitized["MaxDamage"] >= sanitized["MinDamage"], (
            f"MaxDamage must be clamped to >= MinDamage, got "
            f"MinDamage={sanitized['MinDamage']}, "
            f"MaxDamage={sanitized['MaxDamage']}"
        )


# ---------------------------------------------------------------------
# Pydantic schemas — direct construction contract
# ---------------------------------------------------------------------


class TestWeaponSchemas:
    """The Pydantic schemas used by generate_structured."""

    def test_weapon_schema_construction(self) -> None:
        w = WeaponSchema(
            ItemId="custom_weapon_test",
            Name="Test",
            Description="Test",
            MinDamage=10,
            MaxDamage=20,
            CritChance=0.05,
            CritMultiplier=2.0,
            Speed=0,
            Type="Sword",
            Texture="Weapons/test",
            DisplayName="Weapon.custom_weapon_test.Name",
        )
        assert w.ItemId == "custom_weapon_test"
        assert w.MaxDamage == 20

    def test_weapon_list_schema_construction(self) -> None:
        weapons = [
            WeaponSchema(
                ItemId=f"custom_weapon_w_{i}",
                Name=f"W{i}",
                Description="x",
                MinDamage=1,
                MaxDamage=2,
                CritChance=0.01,
                CritMultiplier=1.0,
                Speed=0,
                Type="Sword",
                Texture=f"Weapons/w_{i}",
                DisplayName=(
                    f"Weapon.custom_weapon_w_{i}.Name"
                ),
            )
            for i in range(3)
        ]
        wrapper = WeaponListSchema(weapons=weapons)
        assert len(wrapper.weapons) == 3

    def test_weapon_list_schema_caps_at_max_times_two(self) -> None:
        # The list schema has max_length=_MAX_WEAPONS * 2 (the
        # runaway-LLM safety cap). Verify Pydantic enforces it.
        from pydantic import ValidationError

        weapons = [
            WeaponSchema(
                ItemId=f"custom_weapon_w_{i}",
                Name=f"W{i}",
                Description="x",
                MinDamage=1,
                MaxDamage=2,
                CritChance=0.01,
                CritMultiplier=1.0,
                Speed=0,
                Type="Sword",
                Texture=f"Weapons/w_{i}",
                DisplayName=(
                    f"Weapon.custom_weapon_w_{i}.Name"
                ),
            )
            for i in range(_MAX_WEAPONS * 2 + 1)
        ]
        with pytest.raises(ValidationError):
            WeaponListSchema(weapons=weapons)


# ---------------------------------------------------------------------
# Round-trip integration test — generate then validate
# ---------------------------------------------------------------------


class TestWeaponDefinitionRoundTrip:
    """generate() then validate_output() must always pass.

    This is the safety net that catches the v168-style 'emits a
    file that fails its own contract' bug. Every code path of
    generate() (LLM success, LLM failure, ValidationError, fallback)
    must produce a GeneratorOutput that validate_output() accepts.
    """

    def test_fallback_path_validates_clean(self) -> None:
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(
                side_effect=RuntimeError("simulated LLM failure")
            ),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_roundtrip_fb",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Fallback path must produce a validating output, "
            f"got errors = {errors}"
        )

    def test_llm_success_path_validates_clean(self) -> None:
        fake_response = {
            "weapons": [
                {
                    "ItemId": "custom_weapon_iron_axe",
                    "Name": "Iron Axe",
                    "Description": "A solid iron axe.",
                    "MinDamage": 25,
                    "MaxDamage": 50,
                    "CritChance": 0.03,
                    "CritMultiplier": 2.5,
                    "Speed": 3,
                    "Type": "Club",
                    "Texture": "Weapons/iron_axe",
                    "DisplayName": "Weapon.custom_weapon_iron_axe.Name",
                }
            ]
        }
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_roundtrip_llm",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"LLM-success path must produce a validating output, "
            f"got errors = {errors}"
        )

    def test_malicious_llm_response_sanitized_then_validates(self) -> None:
        # A malicious LLM tries to smuggle path-traversal,
        # out-of-envelope values, and a non-enum Type. The
        # _sanitize_weapon_row post-parse step must clamp every
        # field so validate_output passes.
        fake_response = {
            "weapons": [
                {
                    "ItemId": "evil_weapon",  # missing prefix
                    "Name": "<script>alert(1)</script>",  # bad chars
                    "Description": "X" * 500,  # too long
                    "MinDamage": 9999,  # out of envelope
                    "MaxDamage": -50,  # out of envelope + < min
                    "CritChance": 99.0,  # out of envelope
                    "CritMultiplier": -5.0,  # out of envelope
                    "Speed": 999,  # out of envelope
                    "Type": "Wand",  # not in enum
                    "Texture": "../../etc/passwd",  # path traversal
                    "DisplayName": "",  # empty → default
                }
            ]
        }
        with patch(
            "generators.packs.stardew_valley.features.weapon_definition"
            ".generate_structured",
            new=AsyncMock(return_value=fake_response),
        ):
            gen = WeaponDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "x",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_weapon_malicious",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Sanitized malicious LLM response must validate "
            f"clean, got errors = {errors}"
        )
        # Verify the prefix was added.
        weapons = out.files[
            "assets/weapon_definition/weapons.json"
        ]["weapons"]
        assert weapons[0]["ItemId"].startswith(
            f"{_WEAPON_TOKEN_PREFIX}_"
        ), (
            f"missing prefix must be added by sanitizer, got "
            f"{weapons[0]['ItemId']!r}"
        )
        # Verify the texture was clamped to the default.
        assert weapons[0]["Texture"] == (
            f"Weapons/{weapons[0]['ItemId']}"
        ), (
            f"path-traversal texture must clamp to default, got "
            f"{weapons[0]['Texture']!r}"
        )


# ---------------------------------------------------------------------
# ContentJsonGenerator — v170 second cooperating generator
# ---------------------------------------------------------------------


def _make_weapons_json(
    weapons: list,
) -> dict:
    """Build a prior_outputs-shaped ``weapons.json`` dict.

    Mirrors what ``WeaponDefinitionDefinitionGenerator.generate()``
    writes into ``prior_outputs['weapon_definition_definition_generator']
    .files['assets/weapon_definition/weapons.json']``.
    """
    return {"weapons": weapons}


def _make_manifest(unique_id: str = "test.weapon.mod") -> dict:
    """Build a prior_outputs-shaped ``manifest.json`` dict."""
    return {
        "Format": _FORMAT_VERSION,
        "UniqueID": unique_id,
        "Name": "Test Weapon Mod",
        "Author": "Agent 3",
        "Version": "1.0.0",
        "Description": "A test mod.",
        "ContentPackFor": {
            "UniqueID": "Pathoschild.ContentPatcher",
        },
    }


def _make_definition_output(
    weapons: list,
    unique_id: str | None = "test.weapon.mod",
) -> GeneratorOutput:
    """Build a prior_outputs-shaped DefinitionGenerator output."""
    out = GeneratorOutput()
    if unique_id is not None:
        out.add_file("manifest.json", _make_manifest(unique_id))
    out.add_file(
        "assets/weapon_definition/weapons.json",
        _make_weapons_json(weapons),
    )
    return out


def _make_cj_inp(
    definition_output: GeneratorOutput | None = None,
    prior_outputs: dict | None = None,
) -> GeneratorInput:
    """Build a GeneratorInput (TypedDict) for the ContentJsonGenerator.

    Returns a plain ``dict`` matching the ``GeneratorInput`` TypedDict
    shape; at runtime TypedDicts ARE dicts so this construction is
    equivalent to the kwarg-constructed form used by the v169 tests.
    The ``cast`` silences Pyright's TypedDict-vs-dict invariance check
    (it does not affect runtime behaviour).
    """
    if prior_outputs is None:
        prior_outputs = {}
        if definition_output is not None:
            prior_outputs[
                "weapon_definition_definition_generator"
            ] = definition_output
    return cast(
        GeneratorInput,
        {
            "prompt": "",
            "hint": {},
            "t2_feedback": "",
            "request_id": "req_test_weapon_cj",
            "game": "stardew_valley",
            "prior_outputs": prior_outputs,
        },
    )


def _build_content_json(
    weapons: list,
    unique_id: str | None = "test.weapon.mod",
) -> dict:
    """Drive ContentJsonGenerator end-to-end and return content.json."""
    definition = _make_definition_output(weapons, unique_id)
    gen = WeaponDefinitionContentJsonGenerator()
    out = asyncio.run(gen.generate(_make_cj_inp(definition)))
    content = out.files["content.json"]
    assert isinstance(content, dict)
    return content


class TestWeaponDefinitionContentJsonBasics:
    """The ContentJsonGenerator class identity and pack registration.

    v170 appended this generator alongside the DefinitionGenerator; it
    runs second and reads the DefinitionGenerator's prior_outputs to
    assemble the final ``content.json``.
    """

    def test_class_identity(self) -> None:
        # The class must declare its name/phase/game metadata so the
        # pack's PhaseGenerators registry can look it up.
        assert (
            WeaponDefinitionContentJsonGenerator.name
            == "weapon_definition_content_json_generator"
        )
        assert (
            WeaponDefinitionContentJsonGenerator.phase
            == "weapon_definition"
        )
        assert (
            WeaponDefinitionContentJsonGenerator.game
            == "stardew_valley"
        )

    def test_content_json_generator_registered_in_phase_branch(self) -> None:
        # The v170 phase branch registers BOTH generators; this test
        # specifically pins that the ContentJsonGenerator is included
        # (not just the DefinitionGenerator).
        from generators.packs.stardew_valley import StardewValleyPack

        pg = StardewValleyPack.get_generators("weapon_definition")
        names = [g.name for g in pg.generators]
        assert (
            "weapon_definition_content_json_generator" in names
        ), (
            f"ContentJsonGenerator must be registered in the "
            f"weapon_definition phase branch per v170; got {names}"
        )
        # execution_order has BOTH names in dependency order.
        assert tuple(pg.execution_order) == (
            "weapon_definition_definition_generator",
            "weapon_definition_content_json_generator",
        ), (
            f"execution_order must pin dependency order (definition "
            f"first, content_json second), got "
            f"{tuple(pg.execution_order)}"
        )

    def test_content_json_generator_is_deterministic_no_llm(self) -> None:
        # The ContentJsonGenerator does NOT call the LLM — it just
        # reads prior_outputs and assembles content.json. We can
        # verify this by passing NO prior_outputs and confirming the
        # output is the deterministic 2-weapon fallback (size 2).
        gen = WeaponDefinitionContentJsonGenerator()
        inp = _make_cj_inp()  # empty prior_outputs
        out = asyncio.run(gen.generate(inp))
        assert out.metadata["weapon_count"] == 2, (
            f"ContentJsonGenerator with no prior_outputs must use "
            f"_fallback_weapon_list() (size 2), got "
            f"weapon_count = {out.metadata['weapon_count']}"
        )


class TestWeaponDefinitionContentJsonValidateOutput:
    """Pin the ``validate_output()`` contract for hand-built outputs.

    The ContentJsonGenerator's ``validate_output()`` enforces:
      - content.json present, is a dict
      - ``Format`` and ``Changes`` top-level keys present
      - ``Changes`` is a non-empty list
      - includes one change with Target == ``"Data/Weapons"``
      - includes one change with Target == ``"Strings/UI"``
      - every Data/Weapons Entries row is a dict carrying all 10
        canonical fields (Name, DisplayName, Description, MinDamage,
        MaxDamage, CritChance, CritMultiplier, Speed, Type, Texture)
      - every row key starts with ``custom_weapon_`` prefix
    """

    @staticmethod
    def _valid_content_json() -> dict:
        return {
            "Format": _FORMAT_VERSION,
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/Weapons",
                    "Entries": {
                        "custom_weapon_test_blade": {
                            "Name": (
                                "Weapon.custom_weapon_test_blade"
                                ".Name"
                            ),
                            "DisplayName": (
                                "Weapon.custom_weapon_test_blade"
                                ".Name"
                            ),
                            "Description": (
                                "Weapon.custom_weapon_test_blade"
                                ".Description"
                            ),
                            "MinDamage": 10,
                            "MaxDamage": 20,
                            "CritChance": 0.05,
                            "CritMultiplier": 2.0,
                            "Speed": 5,
                            "Type": "Sword",
                            "Texture": "Weapons/test_blade",
                        },
                    },
                },
                {
                    "Action": "EditData",
                    "Target": "Strings/UI",
                    "Entries": {
                        "Weapon.custom_weapon_test_blade.Name": (
                            "Test Blade"
                        ),
                    },
                },
            ],
        }

    def test_valid_content_json_validates_clean(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        out = GeneratorOutput()
        out.add_file("content.json", self._valid_content_json())
        errors = gen.validate_output(out)
        assert errors == [], (
            f"valid content.json must validate clean, got "
            f"errors = {errors}"
        )

    def test_missing_format_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        del content["Format"]
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any("Format" in e for e in errors), (
            f"missing Format must be flagged, got errors = {errors}"
        )

    def test_missing_changes_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        del content["Changes"]
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any("Changes" in e for e in errors), (
            f"missing Changes must be flagged, got errors = {errors}"
        )

    def test_empty_changes_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        content["Changes"] = []
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any("Changes" in e for e in errors), (
            f"empty Changes must be flagged, got errors = {errors}"
        )

    def test_missing_data_weapons_change_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        # Replace the Data/Weapons change with a different target.
        content["Changes"][0]["Target"] = "Data/NotWeapons"
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any(
            "Data/Weapons" in e for e in errors
        ), (
            f"missing Data/Weapons change must be flagged, got "
            f"errors = {errors}"
        )

    def test_missing_strings_ui_change_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        content["Changes"][1]["Target"] = "Strings/Other"
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any(
            "Strings/UI" in e for e in errors
        ), (
            f"missing Strings/UI change must be flagged, got "
            f"errors = {errors}"
        )

    def test_per_row_missing_field_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        # Drop ``CritMultiplier`` from the only row.
        row = content["Changes"][0]["Entries"][
            "custom_weapon_test_blade"
        ]
        assert isinstance(row, dict)
        del row["CritMultiplier"]
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any(
            "CritMultiplier" in e for e in errors
        ), (
            f"per-row missing CritMultiplier must be flagged, got "
            f"errors = {errors}"
        )

    def test_row_key_missing_prefix_flagged(self) -> None:
        gen = WeaponDefinitionContentJsonGenerator()
        content = self._valid_content_json()
        # Rename the row key so it lacks the ``custom_weapon_`` prefix.
        old_entries = content["Changes"][0]["Entries"]
        assert isinstance(old_entries, dict)
        old_row = old_entries["custom_weapon_test_blade"]
        assert isinstance(old_row, dict)
        new_entries: dict[str, object] = {"evil_blade": old_row}
        content["Changes"][0]["Entries"] = new_entries
        out = GeneratorOutput()
        out.add_file("content.json", content)
        errors = gen.validate_output(out)
        assert any(
            "prefix" in e for e in errors
        ), (
            f"row key missing custom_weapon_ prefix must be "
            f"flagged, got errors = {errors}"
        )


class TestWeaponDefinitionContentJsonRoundTrip:
    """``generate()`` then ``validate_output()`` must always pass.

    Integration test pattern (the v168 safety net). Three sub-tests:
    happy path with 1 weapon, happy path with 3 weapons, and empty
    prior_outputs (fallback path).
    """

    @staticmethod
    def _weapon(
        token: str,
        *,
        min_damage: int = 10,
        max_damage: int = 20,
        texture: str = "Weapons/default",
    ) -> dict:
        return {
            "ItemId": f"custom_weapon_{token}",
            "Name": token.replace("_", " ").title(),
            "Description": f"A {token} weapon.",
            "MinDamage": min_damage,
            "MaxDamage": max_damage,
            "CritChance": _DEFAULT_CRIT_CHANCE,
            "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
            "Speed": _DEFAULT_SPEED,
            "Type": "Sword",
            "Texture": texture,
            "DisplayName": (
                f"Weapon.custom_weapon_{token}.Name"
            ),
        }

    def test_one_weapon_round_trip_validates_clean(self) -> None:
        weapons = [self._weapon("solo_blade")]
        gen = WeaponDefinitionContentJsonGenerator()
        definition = _make_definition_output(weapons)
        out = asyncio.run(gen.generate(_make_cj_inp(definition)))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"1-weapon round-trip must validate clean, got "
            f"errors = {errors}"
        )
        assert out.metadata["weapon_count"] == 1, (
            f"weapon_count metadata must match input size, got "
            f"{out.metadata['weapon_count']}"
        )

    def test_three_weapons_round_trip_validates_clean(self) -> None:
        weapons = [
            self._weapon("alpha_blade"),
            self._weapon("beta_blade"),
            self._weapon("gamma_blade"),
        ]
        gen = WeaponDefinitionContentJsonGenerator()
        definition = _make_definition_output(weapons)
        out = asyncio.run(gen.generate(_make_cj_inp(definition)))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"3-weapon round-trip must validate clean, got "
            f"errors = {errors}"
        )
        assert out.metadata["weapon_count"] == 3, (
            f"weapon_count metadata must match input size, got "
            f"{out.metadata['weapon_count']}"
        )

    def test_empty_prior_outputs_uses_fallback_size_2(self) -> None:
        # No definition generator output → ContentJsonGenerator must
        # fall back to ``_fallback_weapon_list()`` (size 2) and the
        # round-trip must still validate clean.
        gen = WeaponDefinitionContentJsonGenerator()
        inp = _make_cj_inp()  # empty prior_outputs
        out = asyncio.run(gen.generate(inp))
        errors = gen.validate_output(out)
        assert errors == [], (
            f"empty-prior_outputs fallback must validate clean, "
            f"got errors = {errors}"
        )
        assert out.metadata["weapon_count"] == 2, (
            f"empty-prior_outputs must use _fallback_weapon_list() "
            f"(size 2), got weapon_count = "
            f"{out.metadata['weapon_count']}"
        )


class TestWeaponDefinitionContentJsonFallbacks:
    """Pin every defensive fallback in the ContentJsonGenerator.

    Three scenarios per the v170 review (lines 67-91):
      1. No manifest_generator prior output → mod_id defaults to
         ``_DEFAULT_MOD_ID.lower()``.
      2. No definition_generator prior output → weapons list defaults
         to ``_fallback_weapon_list()`` (size 2).
      3. Non-list ``weapons`` field in the weapons.json → fallback
         fires.
    """

    def test_no_manifest_prior_uses_default_mod_id(self) -> None:
        # Pass a definition output with NO manifest in prior_outputs
        # (the manifest_generator key is absent). The ContentJsonGenerator
        # must default mod_id to _DEFAULT_MOD_ID.lower().
        weapons = [
            {
                "ItemId": "custom_weapon_axe",
                "Name": "Axe",
                "Description": "An axe.",
                "MinDamage": 5,
                "MaxDamage": 10,
                "CritChance": _DEFAULT_CRIT_CHANCE,
                "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
                "Speed": _DEFAULT_SPEED,
                "Type": "Club",
                "Texture": "Weapons/axe",
                "DisplayName": "Weapon.custom_weapon_axe.Name",
            },
        ]
        definition = GeneratorOutput()  # NO manifest.json
        definition.add_file(
            "assets/weapon_definition/weapons.json",
            _make_weapons_json(weapons),
        )
        gen = WeaponDefinitionContentJsonGenerator()
        out = asyncio.run(gen.generate(_make_cj_inp(definition)))
        assert out.metadata["mod_id"] == _DEFAULT_MOD_ID.lower(), (
            f"no manifest_generator prior must default mod_id to "
            f"_DEFAULT_MOD_ID.lower() = {_DEFAULT_MOD_ID.lower()!r}, "
            f"got {out.metadata['mod_id']!r}"
        )

    def test_no_definition_prior_uses_fallback_weapons(self) -> None:
        # No definition_generator prior output → weapons list defaults
        # to ``_fallback_weapon_list()`` (size 2).
        gen = WeaponDefinitionContentJsonGenerator()
        inp = _make_cj_inp()  # empty prior_outputs
        out = asyncio.run(gen.generate(inp))
        assert out.metadata["weapon_count"] == 2, (
            f"no definition_generator prior must use "
            f"_fallback_weapon_list() (size 2), got "
            f"weapon_count = {out.metadata['weapon_count']}"
        )
        changes = out.files["content.json"]["Changes"]
        weapon_entries = changes[0]["Entries"]
        ids = list(weapon_entries.keys())
        assert "custom_weapon_wood_sword" in ids
        assert "custom_weapon_iron_dagger" in ids

    def test_non_list_weapons_field_falls_back(self) -> None:
        # Pass a weapons.json where the ``weapons`` key is a STRING
        # (not a list). The ContentJsonGenerator filters to dicts
        # only and falls back to _fallback_weapon_list() because the
        # post-filter list is empty.
        definition = GeneratorOutput()
        definition.add_file(
            "assets/weapon_definition/weapons.json",
            {"weapons": "this-is-not-a-list"},
        )
        gen = WeaponDefinitionContentJsonGenerator()
        out = asyncio.run(gen.generate(_make_cj_inp(definition)))
        assert out.metadata["weapon_count"] == 2, (
            f"non-list weapons field must trigger fallback (size 2), "
            f"got weapon_count = {out.metadata['weapon_count']}"
        )
        # And the round-trip still validates.
        errors = gen.validate_output(out)
        assert errors == [], (
            f"non-list fallback output must validate clean, got "
            f"errors = {errors}"
        )


class TestWeaponDefinitionContentJsonSanitizers:
    """The v169 sanitizers must fire inside the ContentJsonGenerator's
    row-construction path.

    Four scenarios per the v171 plan section 4e:
      - MaxDamage clamp (MinDamage > MaxDamage → MaxDamage := MinDamage)
      - Empty DisplayName default → falls back to the title-cased
        bare token (stripped of ``custom_weapon_``)
      - Path-traversal texture clamp (e.g. ``../../../etc/passwd``)
      - Row-key prefix: every emitted key starts with ``custom_weapon_``
    """

    def test_maxdamage_clamped_to_at_least_min(self) -> None:
        weapons = [
            {
                "ItemId": "custom_weapon_clamp_blade",
                "Name": "Clamp Blade",
                "Description": "A blade that clamps damage.",
                "MinDamage": 10,
                "MaxDamage": 5,  # BELOW MinDamage
                "CritChance": _DEFAULT_CRIT_CHANCE,
                "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
                "Speed": _DEFAULT_SPEED,
                "Type": "Sword",
                "Texture": "Weapons/clamp_blade",
                "DisplayName": (
                    "Weapon.custom_weapon_clamp_blade.Name"
                ),
            },
        ]
        content = _build_content_json(weapons)
        row = content["Changes"][0]["Entries"][
            "custom_weapon_clamp_blade"
        ]
        assert isinstance(row, dict)
        assert row["MaxDamage"] >= row["MinDamage"], (
            f"MaxDamage below MinDamage must be clamped to >= "
            f"MinDamage; got MinDamage={row['MinDamage']} "
            f"MaxDamage={row['MaxDamage']}"
        )
        assert row["MaxDamage"] == 10, (
            f"specific clamp: MinDamage=10, MaxDamage=5 → "
            f"emitted MaxDamage must equal 10, got "
            f"{row['MaxDamage']}"
        )

    def test_empty_display_name_falls_back_to_title_cased_token(
        self,
    ) -> None:
        # The fallback applies inside ContentJsonGenerator when the
        # sanitized display name is empty (it strips ``custom_weapon_``
        # and title-cases the bare token).
        weapons = [
            {
                "ItemId": "custom_weapon_ghost_blade",
                "Name": "",  # empty → sanitizer returns ""
                "Description": "A ghost blade.",
                "MinDamage": 5,
                "MaxDamage": 10,
                "CritChance": _DEFAULT_CRIT_CHANCE,
                "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
                "Speed": _DEFAULT_SPEED,
                "Type": "Dagger",
                "Texture": "Weapons/ghost_blade",
                "DisplayName": "",  # also empty
            },
        ]
        content = _build_content_json(weapons)
        # The Strings/UI change contains the fallback display string
        # for the empty-display-name weapon. We look it up by checking
        # that "Ghost Blade" (title-cased bare token) appears as a
        # value in the Strings/UI entries.
        strings_change = next(
            c
            for c in content["Changes"]
            if isinstance(c, dict)
            and c.get("Target") == "Strings/UI"
        )
        assert isinstance(strings_change, dict)
        strings_entries = strings_change.get("Entries", {})
        assert isinstance(strings_entries, dict)
        # At least one Strings/UI value should be the title-cased
        # bare token "Ghost Blade" (stripped of custom_weapon_).
        values = list(strings_entries.values())
        assert "Ghost Blade" in values, (
            f"empty DisplayName must fall back to title-cased bare "
            f"token 'Ghost Blade'; got Strings/UI values = {values}"
        )

    def test_path_traversal_texture_clamps_to_default(self) -> None:
        # _sanitize_texture path-traversal guard is exercised in the
        # ContentJsonGenerator context (not just the DefinitionGenerator
        # context). The Texture field is read via _sanitize_texture
        # with the _DEFAULT_TEXTURE_TEMPLATE fallback.
        malicious_texture = "../../../etc/passwd"
        weapons = [
            {
                "ItemId": "custom_weapon_trav_blade",
                "Name": "Trav Blade",
                "Description": "A blade that tries to traverse.",
                "MinDamage": 5,
                "MaxDamage": 10,
                "CritChance": _DEFAULT_CRIT_CHANCE,
                "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
                "Speed": _DEFAULT_SPEED,
                "Type": "Dagger",
                "Texture": malicious_texture,
                "DisplayName": (
                    "Weapon.custom_weapon_trav_blade.Name"
                ),
            },
        ]
        content = _build_content_json(weapons)
        row = content["Changes"][0]["Entries"][
            "custom_weapon_trav_blade"
        ]
        assert isinstance(row, dict)
        # _sanitize_texture clamps path-traversal input to the
        # _DEFAULT_TEXTURE_TEMPLATE.format(token=...) value.
        expected_texture = _DEFAULT_TEXTURE_TEMPLATE.format(
            weapon_token="custom_weapon_trav_blade"
        )
        assert row["Texture"] == expected_texture, (
            f"path-traversal Texture must clamp to "
            f"_DEFAULT_TEXTURE_TEMPLATE (={expected_texture!r}), "
            f"got {row['Texture']!r}"
        )

    def test_every_row_key_starts_with_custom_weapon_prefix(self) -> None:
        # Round-trip emission of 3 weapons — every emitted Data/Weapons
        # Entries key must start with the ``custom_weapon_`` prefix.
        weapons = [
            {
                "ItemId": f"custom_weapon_blade_{i}",
                "Name": f"Blade {i}",
                "Description": f"Blade number {i}.",
                "MinDamage": 5,
                "MaxDamage": 10 + i,
                "CritChance": _DEFAULT_CRIT_CHANCE,
                "CritMultiplier": _DEFAULT_CRIT_MULTIPLIER,
                "Speed": _DEFAULT_SPEED,
                "Type": "Sword",
                "Texture": f"Weapons/blade_{i}",
                "DisplayName": (
                    f"Weapon.custom_weapon_blade_{i}.Name"
                ),
            }
            for i in range(3)
        ]
        content = _build_content_json(weapons)
        weapons_change = next(
            c
            for c in content["Changes"]
            if isinstance(c, dict)
            and c.get("Target") == "Data/Weapons"
        )
        assert isinstance(weapons_change, dict)
        entries = weapons_change.get("Entries", {})
        assert isinstance(entries, dict)
        keys = list(entries.keys())
        assert len(keys) == 3, (
            f"3 input weapons must emit 3 Data/Weapons row keys, "
            f"got {len(keys)}: {keys}"
        )
        for key in keys:
            assert key.startswith(f"{_WEAPON_TOKEN_PREFIX}_"), (
                f"every row key must start with "
                f"{_WEAPON_TOKEN_PREFIX!r}_ prefix, got {key!r}"
            )