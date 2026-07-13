"""Tests for the tool_definition pack (DefinitionGenerator + ContentJsonGenerator).

v172 round landed the first cooperating generator of the two-generator
``tool_definition`` pack: the ``ToolDefinitionDefinitionGenerator``
(LLM-driven row producer with sanitizers, Pydantic schemas, and a
3-tool deterministic fallback). v173 round appended the
``ToolDefinitionContentJsonGenerator`` (deterministic content.json
assembler that reads the DefinitionGenerator's ``prior_outputs`` and
emits the ``Data/Tools`` + ``Strings/UI`` EditData changes).
v174 round writes this test file (NEW, separate file from the
weapon_definition test file because tool_definition is a separate
pack — different phase, different per-row shape, different
sanitizers).

Mirrors the v88 ``tests/test_weather_event_generator.py``, v101
``tests/test_weather_manifest_generator.py``, and v169 / v171
``tests/test_weapon_definition_generator.py`` recipes:

- Hermetic — does not import app.config, does not talk to
  Postgres/Redis/LLM. Runs in < 100ms.
- LLM success path: patch ``generate_structured`` to return a
  crafted response, verify the generator emits it.
- LLM fallback path: patch ``generate_structured`` to raise,
  verify the 3-tool hardcoded fallback fires.
- ``validate_output`` contract pinning: required keys,
  ``custom_tool_`` prefix detection, content.json
  shape (Format + 2 Changes, every row key prefixed ``custom_tool_``).
- Sanitizer hardening: AttachmentSlots clamp,
  ``_sanitize_texture`` path-traversal guard rejects ``..``
  segments. The ContentJsonGenerator exercises these sanitizers
  indirectly via its row-construction path
  (AttachmentSlots clamp, empty DisplayName default,
  path-traversal texture clamp).
- Round-trip integration test: ``generate()`` then
  ``validate_output()`` must always pass.

The pack file is at
``generators/packs/stardew_valley/features/tool_definition/__init__.py``.
The deterministic fallback is the 3 curated tools (Bronze Pickaxe,
Maple Hoe, Copper Axe) defined in ``_fallback_tool_list``.
"""
from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.tool_definition import (
    ToolDefinitionDefinitionGenerator,
    ToolDefinitionContentJsonGenerator,
    ToolSchema,
    ToolListSchema,
    _MIN_TOOLS,
    _MAX_TOOLS,
    _DEFAULT_TOOLS,
    _TOOL_TOKEN_PREFIX,
    _TOOL_TOKEN_MAX_LEN,
    _DISPLAY_NAME_MAX_LEN,
    _DESCRIPTION_MAX_LEN,
    _ASSET_PATH_MAX_LEN,
    _MIN_ATTACHMENT_SLOTS,
    _MAX_ATTACHMENT_SLOTS,
    _DEFAULT_ATTACHMENT_SLOTS,
    _DEFAULT_MOD_ID,
    _DEFAULT_TEXTURE_TEMPLATE,
    _NAME_KEY_TEMPLATE,
    _DESCRIPTION_KEY_TEMPLATE,
    _FORMAT_VERSION,
    _sanitize_tool_token,
    _sanitize_attachment_slots,
    _sanitize_display_name,
    _sanitize_description,
    _sanitize_texture,
    _sanitize_count,
    _sanitize_tool_row,
    _fallback_tool,
    _fallback_tool_list,
)


# ---------------------------------------------------------------------
# Phase registration / generator class basics
# ---------------------------------------------------------------------


class TestToolDefinitionGeneratorBasics:
    """The DefinitionGenerator + ContentJsonGenerator class identity
    and pack registration.

    v172 landed the DefinitionGenerator. v173 appended the
    ContentJsonGenerator. v174 pins that BOTH are registered in
    the ``get_generators("tool_definition")`` branch and that
    ``execution_order`` has both names in dependency order
    (definition first, content_json second).
    """

    def test_definition_generator_identity(self) -> None:
        assert (
            ToolDefinitionDefinitionGenerator.name
            == "tool_definition_definition_generator"
        ), (
            f"DefinitionGenerator.name must equal "
            f"'tool_definition_definition_generator', got "
            f"{ToolDefinitionDefinitionGenerator.name!r}"
        )
        assert (
            ToolDefinitionDefinitionGenerator.phase
            == "tool_definition"
        ), (
            f"DefinitionGenerator.phase must equal "
            f"'tool_definition', got "
            f"{ToolDefinitionDefinitionGenerator.phase!r}"
        )
        assert (
            ToolDefinitionDefinitionGenerator.game
            == "stardew_valley"
        ), (
            f"DefinitionGenerator.game must equal "
            f"'stardew_valley', got "
            f"{ToolDefinitionDefinitionGenerator.game!r}"
        )

    def test_content_json_generator_identity(self) -> None:
        assert (
            ToolDefinitionContentJsonGenerator.name
            == "tool_definition_content_json_generator"
        ), (
            f"ContentJsonGenerator.name must equal "
            f"'tool_definition_content_json_generator', got "
            f"{ToolDefinitionContentJsonGenerator.name!r}"
        )
        assert (
            ToolDefinitionContentJsonGenerator.phase
            == "tool_definition"
        ), (
            f"ContentJsonGenerator.phase must equal "
            f"'tool_definition', got "
            f"{ToolDefinitionContentJsonGenerator.phase!r}"
        )
        assert (
            ToolDefinitionContentJsonGenerator.game
            == "stardew_valley"
        ), (
            f"ContentJsonGenerator.game must equal "
            f"'stardew_valley', got "
            f"{ToolDefinitionContentJsonGenerator.game!r}"
        )

    def test_tool_definition_phase_is_registered(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack

        assert "tool_definition" in StardewValleyPack.list_phases(), (
            "StardewValleyPack.supported_phases must include "
            "'tool_definition' after the v172 port; got list = "
            f"{StardewValleyPack.list_phases()}"
        )

    def test_get_generators_returns_both_generators(self) -> None:
        from generators.packs.stardew_valley import StardewValleyPack

        pg = StardewValleyPack.get_generators("tool_definition")
        names = [g.name for g in pg.generators]
        assert names == [
            "tool_definition_definition_generator",
            "tool_definition_content_json_generator",
        ], (
            f"v173 must register BOTH generators (definition first, "
            f"content_json second); got {names}"
        )
        assert list(pg.execution_order) == [
            "tool_definition_definition_generator",
            "tool_definition_content_json_generator",
        ], (
            f"v173 execution_order must be a list of both "
            f"generator names in dependency order, got "
            f"{list(pg.execution_order)}"
        )


# ---------------------------------------------------------------------
# validate_output contract pinning — DefinitionGenerator
# ---------------------------------------------------------------------


class TestToolDefinitionGeneratorValidateOutput:
    """validate_output pins the per-row shape contract for the
    DefinitionGenerator's emitted tools.json.

    v174a covered the FIRST HALF (file-level missing/empty/non-list
    cases). v179 recovers the v176-approved per-row missing-field,
    invalid-range, invalid-prefix, duplicate-ItemId, and
    non-dict-item cases.
    """

    def test_flags_missing_tools_json(self) -> None:
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any(
            "assets/tool_definition/tools.json missing" in e
            for e in errors
        ), (
            f"validate_output must flag missing tools.json, "
            f"got errors = {errors}"
        )

    def test_flags_non_list_tools_field(self) -> None:
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": "not a list"},
        )
        errors = gen.validate_output(out)
        assert any(
            "tools list missing or too short" in e
            for e in errors
        ), (
            f"validate_output must flag non-list 'tools' field, "
            f"got errors = {errors}"
        )

    def test_flags_empty_tools_list(self) -> None:
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": []},
        )
        errors = gen.validate_output(out)
        assert any(
            "tools list missing or too short" in e
            for e in errors
        ), (
            f"validate_output must flag empty tools list, "
            f"got errors = {errors}"
        )

    def test_flags_per_row_missing_itemid(self) -> None:
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {
                "tools": [
                    {
                        # ItemId intentionally omitted
                        "Name": "Bronze Pickaxe",
                        "Description": "A bronze pickaxe.",
                        "AttachmentSlots": 2,
                        "Texture": "Tools/custom_tool_bronze_pickaxe",
                        "DisplayName": (
                            "Tool.custom_tool_bronze_pickaxe.Name"
                        ),
                    }
                ]
            },
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'ItemId'" in e for e in errors
        ), (
            f"validate_output must flag missing ItemId field, "
            f"got errors = {errors}"
        )

    def test_passes_with_valid_tools(self) -> None:
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": _fallback_tool_list()},
        )
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Fallback list (3 tools, all 6 fields, all prefixed) "
            f"must produce no validation errors, got errors = "
            f"{errors}"
        )

    def test_flags_per_row_missing_name(self) -> None:
        # Remove ``Name`` from a fresh fallback row and assert
        # validate_output flags it via the required-field loop
        # (impl lines 632-644).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        del row["Name"]
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'Name'" in e for e in errors
        ), (
            f"validate_output must flag missing 'Name' field "
            f"in a per-row tools list, got errors = {errors}"
        )

    def test_flags_per_row_missing_texture(self) -> None:
        # Remove ``Texture`` from a fresh fallback row and assert
        # validate_output flags it via the required-field loop
        # (impl lines 632-644).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        del row["Texture"]
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'Texture'" in e for e in errors
        ), (
            f"validate_output must flag missing 'Texture' field "
            f"in a per-row tools list, got errors = {errors}"
        )

    def test_flags_per_row_missing_attachment_slots(self) -> None:
        # Remove ``AttachmentSlots`` from a fresh fallback row and
        # assert validate_output flags it via the required-field
        # loop (impl lines 632-644).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        del row["AttachmentSlots"]
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'AttachmentSlots'" in e for e in errors
        ), (
            f"validate_output must flag missing 'AttachmentSlots' "
            f"field in a per-row tools list, got errors = {errors}"
        )

    def test_flags_per_row_missing_description(self) -> None:
        # Remove ``Description`` from a fresh fallback row and
        # assert validate_output flags it via the required-field
        # loop (impl lines 632-644).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        del row["Description"]
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'Description'" in e for e in errors
        ), (
            f"validate_output must flag missing 'Description' "
            f"field in a per-row tools list, got errors = {errors}"
        )

    def test_flags_per_row_missing_displayname(self) -> None:
        # Remove ``DisplayName`` from a fresh fallback row and
        # assert validate_output flags it via the required-field
        # loop (impl lines 632-644).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        del row["DisplayName"]
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'DisplayName'" in e for e in errors
        ), (
            f"validate_output must flag missing 'DisplayName' "
            f"field in a per-row tools list, got errors = {errors}"
        )

    def test_flags_per_row_invalid_attachment_slots_too_high(self) -> None:
        # Set ``AttachmentSlots=10`` (above the vanilla SDV cap
        # of 4) on a fresh fallback row and assert validate_output
        # flags it via the range check (impl lines 659-670).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        row["AttachmentSlots"] = 10
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "AttachmentSlots 10 out of range [0, 4]" in e
            for e in errors
        ), (
            f"validate_output must flag AttachmentSlots=10 as out "
            f"of range [0, 4], got errors = {errors}"
        )

    def test_flags_per_row_invalid_attachment_slots_negative(self) -> None:
        # Set ``AttachmentSlots=-1`` (below the vanilla SDV floor
        # of 0) on a fresh fallback row and assert validate_output
        # flags it via the range check (impl lines 659-670).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        row["AttachmentSlots"] = -1
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "AttachmentSlots -1 out of range [0, 4]" in e
            for e in errors
        ), (
            f"validate_output must flag AttachmentSlots=-1 as out "
            f"of range [0, 4], got errors = {errors}"
        )

    def test_flags_per_row_invalid_prefix(self) -> None:
        # Set ``ItemId='bronze_pickaxe'`` (no ``custom_tool_``
        # prefix) on a fresh fallback row and assert
        # validate_output flags it via the prefix check
        # (impl lines 645-658).
        gen = ToolDefinitionDefinitionGenerator()
        row = _fallback_tool_list()[0]
        row["ItemId"] = "bronze_pickaxe"
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": [row]},
        )
        errors = gen.validate_output(out)
        assert any(
            "missing 'custom_tool_' prefix" in e
            for e in errors
        ), (
            f"validate_output must flag ItemId='bronze_pickaxe' "
            f"as missing the 'custom_tool_' prefix, got errors = "
            f"{errors}"
        )

    def test_flags_per_row_duplicate_itemid(self) -> None:
        # Two distinct dicts sharing one valid ``ItemId`` must
        # trigger the duplicate-ItemId check (case-insensitive,
        # impl lines 645-652). The two rows use different
        # non-required fields so the only error is the
        # duplicate-token one.
        gen = ToolDefinitionDefinitionGenerator()
        rows = _fallback_tool_list()[:2]
        rows[1] = {
            "ItemId": rows[0]["ItemId"],
            "Name": "Renamed Pickaxe",
            "Description": "A renamed bronze pickaxe.",
            "AttachmentSlots": 1,
            "Texture": "Tools/custom_tool_bronze_pickaxe_dup",
            "DisplayName": (
                "Tool.custom_tool_bronze_pickaxe.Name"
            ),
        }
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": rows},
        )
        errors = gen.validate_output(out)
        assert any(
            "duplicate ItemId" in e for e in errors
        ), (
            f"validate_output must flag duplicate ItemId across "
            f"two rows, got errors = {errors}"
        )

    def test_flags_per_row_non_dict_item(self) -> None:
        # A one-element tools list whose item is a string (not a
        # dict) must trigger the per-row non-dict check
        # (impl lines 625-631). The list still has 1 element so
        # the list-length gate at lines 614-622 passes.
        gen = ToolDefinitionDefinitionGenerator()
        out = GeneratorOutput()
        out.add_file(
            "assets/tool_definition/tools.json",
            {"tools": ["not a dict"]},
        )
        errors = gen.validate_output(out)
        assert any(
            "each tool item must be a dict" in e
            for e in errors
        ), (
            f"validate_output must flag a non-dict tools-list "
            f"item, got errors = {errors}"
        )


# ---------------------------------------------------------------------
# Helpers — used by this file's later classes (v174b-l) and pinned
# here in v174a so the rest of the file can rely on them.
# ---------------------------------------------------------------------


def _make_tools_json(
    n_tools: int = 3,
    prefix: str = "custom_tool_",
) -> dict:
    return {
        "tools": [
            _fallback_tool(
                item_id=f"{prefix}test_tool_{i}",
                name=f"Test Tool {i}",
                description=f"Test tool number {i}.",
                attachment_slots=2,
            )
            for i in range(n_tools)
        ]
    }


def _make_manifest(unique_id: str = "custom.tooldefinition") -> dict:
    return {
        "Format": _FORMAT_VERSION,
        "UniqueID": unique_id,
        "Name": "Test Tool Mod",
        "Author": "Agent 3",
        "Version": "1.0.0",
        "Description": "A test tool definition mod.",
        "ContentPackFor": {
            "UniqueID": "Pathoschild.ContentPatcher",
        },
    }


def _make_definition_input(request_id: str) -> GeneratorInput:
    return {
        "prompt": "a custom set of tools",
        "hint": {},
        "t2_feedback": "",
        "request_id": request_id,
        "game": "stardew_valley",
        "prior_outputs": {},
    }


# ---------------------------------------------------------------------
# LLM-success path — DefinitionGenerator
# ---------------------------------------------------------------------


class TestToolDefinitionGeneratorLLMSuccess:
    """The LLM returns schema-valid rows which are sanitized and emitted."""

    _REQUIRED_KEYS = {
        "ItemId",
        "Name",
        "Texture",
        "AttachmentSlots",
        "Description",
        "DisplayName",
    }

    def test_generate_returns_sanitized_tools_from_llm(self) -> None:
        rows = _fallback_tool_list()
        rows[0].update(
            {
                "ItemId": "bronze_pickaxe",
                "Name": "  Bronze   Pickaxe  ",
                "AttachmentSlots": 99,
                "Texture": "../escape.png",
                "DisplayName": " ",
            }
        )
        gen = ToolDefinitionDefinitionGenerator()
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"tools": rows}),
        ):
            out = asyncio.run(
                gen.generate(_make_definition_input("req_tool_success"))
            )

        tools_data = cast(
            dict[str, object],
            out.files["assets/tool_definition/tools.json"],
        )
        tools = cast(list[dict[str, object]], tools_data["tools"])
        assert len(tools) == 3, f"Expected 3 sanitized tools, got {tools!r}"
        assert all(set(tool) == self._REQUIRED_KEYS for tool in tools), (
            f"Every emitted tool must have exactly the six required keys: {tools!r}"
        )
        assert tools[0] == {
            "ItemId": "custom_tool_bronze_pickaxe",
            "Name": "Bronze Pickaxe",
            "Description": (
                "A sturdy bronze-headed pickaxe — breaks the toughest "
                "rocks in the mines."
            ),
            "AttachmentSlots": 4,
            "Texture": "Tools/custom_tool_bronze_pickaxe",
            "DisplayName": "Tool.custom_tool_bronze_pickaxe.Name",
        }, f"First LLM row was not sanitized as expected: {tools[0]!r}"
        assert out.metadata == {
            "tool_count": 3,
            "tool_ids": [
                "custom_tool_bronze_pickaxe",
                "custom_tool_maple_hoe",
                "custom_tool_copper_axe",
            ],
        }, f"Metadata must describe the sanitized rows: {out.metadata!r}"
        assert gen.validate_output(out) == [], (
            f"Sanitized LLM output must validate: {gen.validate_output(out)!r}"
        )

    def test_generate_truncates_to_max_tools(self) -> None:
        rows = _make_tools_json(5)["tools"]
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"tools": rows}),
        ):
            out = asyncio.run(
                ToolDefinitionDefinitionGenerator().generate(
                    _make_definition_input("req_tool_truncation")
                )
            )

        tools_data = cast(
            dict[str, object],
            out.files["assets/tool_definition/tools.json"],
        )
        tools = cast(list[dict[str, object]], tools_data["tools"])
        expected_ids = [f"custom_tool_test_tool_{i}" for i in range(3)]
        assert len(tools) == _MAX_TOOLS, (
            f"Five schema-valid rows must truncate to {_MAX_TOOLS}, got {tools!r}"
        )
        assert out.metadata["tool_ids"] == expected_ids, (
            f"Metadata must retain only the first three IDs: {out.metadata!r}"
        )

    def test_generate_emits_valid_tools_when_llm_returns_min_tools(self) -> None:
        rows = _fallback_tool_list()[:1]
        gen = ToolDefinitionDefinitionGenerator()
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"tools": rows}),
        ):
            out = asyncio.run(
                gen.generate(_make_definition_input("req_tool_minimum"))
            )

        tools_data = cast(
            dict[str, object],
            out.files["assets/tool_definition/tools.json"],
        )
        tools = cast(list[dict[str, object]], tools_data["tools"])
        assert len(tools) == _MIN_TOOLS, (
            f"One valid row must satisfy _MIN_TOOLS={_MIN_TOOLS}: {tools!r}"
        )
        assert out.metadata == {
            "tool_count": _MIN_TOOLS,
            "tool_ids": ["custom_tool_bronze_pickaxe"],
        }, f"Minimum-row metadata is inconsistent: {out.metadata!r}"
        assert gen.validate_output(out) == [], (
            f"Minimum valid LLM response must validate: {gen.validate_output(out)!r}"
        )

    def test_generate_calls_sanitize_tool_row_per_row(self) -> None:
        rows = _fallback_tool_list()[:2]
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"tools": rows}),
        ), patch(
            "generators.packs.stardew_valley.features.tool_definition"
            "._sanitize_tool_row",
            side_effect=lambda row: row,
        ) as sanitize_row:
            out = asyncio.run(
                ToolDefinitionDefinitionGenerator().generate(
                    _make_definition_input("req_tool_sanitize_spy")
                )
            )

        called_rows = [call.args[0] for call in sanitize_row.call_args_list]
        assert sanitize_row.call_count == 2, (
            f"Sanitizer must run once per retained row, calls={called_rows!r}"
        )
        assert called_rows[0] is not called_rows[1], (
            f"Sanitizer calls must receive distinct row dicts: {called_rows!r}"
        )
        assert all(set(row) == self._REQUIRED_KEYS for row in called_rows), (
            f"Every sanitizer call must receive all six keys: {called_rows!r}"
        )
        assert out.metadata["tool_count"] == 2, (
            f"Spy path must still emit both retained rows: {out.metadata!r}"
        )


# ---------------------------------------------------------------------
# LLM-failure fallback path — DefinitionGenerator
# ---------------------------------------------------------------------


class TestToolDefinitionGeneratorFallback:
    """The LLM-failure fallback path of
    ``ToolDefinitionDefinitionGenerator.generate()``.

    v178 — the dual of v177's ``TestToolDefinitionGeneratorLLMSuccess``.
    Instead of patching ``generate_structured`` to RETURN a valid
    response (v177), v178 patches it to RAISE one of the 4 caught
    exception types (``ValueError``, ``RuntimeError``, ``IOError``,
    ``ValidationError``) and asserts the generator emits the 3-tool
    hardcoded fallback defined in ``_fallback_tool_list()``.

    Mirrors ``TestWeaponDefinitionGeneratorFallback`` from v171.
    """

    def test_generate_falls_back_to_default_list_when_llm_raises_value_error(self) -> None:
        # ``generate_structured`` raises ``ValueError`` (simulated
        # LLM malformed-output scenario). The generator must catch
        # it (per impl lines 561-563) and fall back to
        # ``_fallback_tool_list()`` (per impl line 569).
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(
                side_effect=ValueError(
                    "simulated LLM malformed output"
                )
            ),
        ):
            gen = ToolDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "a custom set of mining tools",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_tool_fallback_value_error",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))

        tools_data = out.files[
            "assets/tool_definition/tools.json"
        ]
        tools = tools_data["tools"]
        assert len(tools) == 3, (
            f"Fallback path must emit exactly 3 tools "
            f"(Bronze Pickaxe, Maple Hoe, Copper Axe) when "
            f"ValueError is raised by the LLM; got {len(tools)} "
            f"tools = {tools!r}"
        )

        for tool in tools:
            for required_key in (
                "ItemId",
                "Name",
                "Texture",
                "AttachmentSlots",
                "Description",
                "DisplayName",
            ):
                assert required_key in tool, (
                    f"Each fallback tool must have all 6 "
                    f"required keys, missing {required_key!r} "
                    f"in tool = {tool!r}"
                )

        assert out.metadata["tool_count"] == 3, (
            f"out.metadata['tool_count'] must equal 3 after "
            f"ValueError fallback; got {out.metadata['tool_count']!r}"
        )
        assert out.metadata["tool_ids"] == [
            "custom_tool_bronze_pickaxe",
            "custom_tool_maple_hoe",
            "custom_tool_copper_axe",
        ], (
            f"out.metadata['tool_ids'] must be the 3 fallback "
            f"ItemIds in order; got {out.metadata['tool_ids']!r}"
        )

        # The fallback list is well-formed: it must pass the
        # validate_output contract (required keys, prefix,
        # range, non-empty, non-dup).
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Fallback output must pass validate_output with "
            f"no errors (the 3-tool list is well-formed); got "
            f"errors = {errors}"
        )

    def test_generate_falls_back_to_default_list_when_llm_returns_malformed(self) -> None:
        # ``generate_structured`` returns a dict that fails
        # ``ToolListSchema(**result)`` validation (LLM returned
        # JSON that doesn't match the schema). The generator
        # must catch the ``ValidationError`` (per impl lines
        # 561-563) and fall back to ``_fallback_tool_list()``.
        # This mirrors the v171 weapon_definition test's
        # ``test_fallback_fires_on_validation_error`` recipe
        # — same pattern, different fallback list.
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(return_value={"tools": "not a list"}),
        ):
            gen = ToolDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "a custom set of gardening tools",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_tool_fallback_validation_error",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))

        tools = out.files[
            "assets/tool_definition/tools.json"
        ]["tools"]
        assert len(tools) == 3, (
            f"Fallback path must emit exactly 3 tools when "
            f"the LLM returns a schema-invalid response "
            f"(caught as ValidationError); got {len(tools)} "
            f"tools = {tools!r}"
        )

        for tool in tools:
            for required_key in (
                "ItemId",
                "Name",
                "Texture",
                "AttachmentSlots",
                "Description",
                "DisplayName",
            ):
                assert required_key in tool, (
                    f"Each fallback tool must have all 6 "
                    f"required keys after ValidationError "
                    f"fallback, missing {required_key!r} in "
                    f"tool = {tool!r}"
                )

        assert out.metadata["tool_count"] == 3, (
            f"out.metadata['tool_count'] must equal 3 after "
            f"ValidationError fallback; got "
            f"{out.metadata['tool_count']!r}"
        )

        # The ValidationError fallback must ALSO pass
        # validate_output (the fallback is well-formed).
        errors = gen.validate_output(out)
        assert errors == [], (
            f"Fallback output after ValidationError must "
            f"pass validate_output with no errors; got "
            f"errors = {errors}"
        )

    def test_generate_fallback_metadata_matches_fallback_tool_ids(self) -> None:
        # ``generate_structured`` raises ``RuntimeError`` (simulated
        # LLM timeout / network-failure scenario). The generator
        # must catch it (per impl lines 561-563) and fall back.
        # This test cross-checks the metadata against a DIRECT
        # call to ``_fallback_tool_list()`` — independent of the
        # impl's internal slicing/sanitization, so any drift in
        # the fallback list or the metadata emitter would surface
        # as a test failure.
        with patch(
            "generators.packs.stardew_valley.features.tool_definition"
            ".generate_structured",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "simulated LLM timeout"
                )
            ),
        ):
            gen = ToolDefinitionDefinitionGenerator()
            inp: GeneratorInput = {
                "prompt": "a custom set of foraging tools",
                "hint": {},
                "t2_feedback": "",
                "request_id": "req_test_tool_fallback_runtime_error",
                "game": "stardew_valley",
                "prior_outputs": {},
            }
            out = asyncio.run(gen.generate(inp))

        assert out.metadata["tool_count"] == 3, (
            f"out.metadata['tool_count'] must equal 3 after "
            f"RuntimeError fallback; got {out.metadata['tool_count']!r}"
        )

        # Cross-check: the 3 ItemIds in metadata match exactly
        # the 3 ItemIds returned by ``_fallback_tool_list()``
        # directly (defensive — if the impl ever slices or
        # dedupes the fallback list, this catches it).
        expected_ids = [
            t["ItemId"] for t in _fallback_tool_list()
        ]
        assert out.metadata["tool_ids"] == expected_ids, (
            f"out.metadata['tool_ids'] must match "
            f"_fallback_tool_list() ItemIds exactly; "
            f"got out.metadata['tool_ids'] = "
            f"{out.metadata['tool_ids']!r}, "
            f"expected = {expected_ids!r}"
        )

        # Each fallback tool's ``Texture`` field must follow the
        # ``Tools/<tool_token>`` shape from the texture template
        # (``_DEFAULT_TEXTURE_TEMPLATE = "Tools/{tool_token}"``).
        for tool in out.files[
            "assets/tool_definition/tools.json"
        ]["tools"]:
            texture = tool["Texture"]
            token = tool["ItemId"]
            expected_texture = (
                _DEFAULT_TEXTURE_TEMPLATE.format(
                    tool_token=token
                )
            )
            assert texture == expected_texture, (
                f"Fallback tool Texture must follow the "
                f"_DEFAULT_TEXTURE_TEMPLATE pattern "
                f"('Tools/{{tool_token}}'); got texture = "
                f"{texture!r}, expected = {expected_texture!r} "
                f"for token = {token!r}"
            )