"""Tests for the T1 Content Patcher schema hardening.

Pins the SMAPI-load-time fixes that the old gates missed: the .test demo
mods all passed T1 (and smapi_validate) yet 7/10 produced CP load warnings
at real-game load time — invalid ``When`` tokens (``DayOfMonth``,
``MailReceived``), ``EditData`` + ``FromFile`` combos, and ``EditMap``
``MapTiles`` entries missing ``Position``. These tests feed known-bad
content.json snippets into ``run_t1`` and assert the gate now FAILS on each
class, while well-formed changes still PASS (no regression).

The checks run in ``_validate_file`` for *every* generator that emits a
content.json, so the tests exercise both the ``content_json_generator``
name and other content-producing generator names.
"""
from __future__ import annotations

from generators.core import GeneratorOutput
from quality.gate_t1 import run_t1


def _out_with(file_path: str, content) -> GeneratorOutput:
    """Build a single-file GeneratorOutput for a synthetic generator."""
    out = GeneratorOutput()
    out.add_file(file_path, content)
    return out


class TestWhenTokens:
    """Invalid ``When`` condition keys must fail T1; valid ones pass."""

    def test_dayofmonth_token_rejected(self) -> None:
        """The shop_channel bug: ``DayOfMonth`` is not a CP ConditionType."""
        content = {
            "Format": "1.29.0",
            "Changes": [{
                "Action": "Load",
                "Target": "Data/X",
                "FromFile": "x.json",
                "When": {"DayOfMonth": 6},
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("DayOfMonth" in e for e in result.errors)

    def test_mailreceived_token_rejected(self) -> None:
        """The shop_channel bug: ``MailReceived`` is not a CP ConditionType."""
        content = {
            "Format": "1.29.0",
            "Changes": [{
                "Action": "Load",
                "Target": "mods/tv/catalog",
                "FromFile": "assets/data/catalog.json",
                "When": {"MailReceived": "tv_shopping_broadcast"},
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("MailReceived" in e for e in result.errors)

    def test_other_content_generator_name_also_checked(self) -> None:
        """The check runs for any generator emitting content.json, not just
        ``content_json_generator`` — the 7/10 bad mods spanned phases."""
        content = [{
            "Action": "Load",
            "Target": "Data/X",
            "FromFile": "x.json",
            "When": {"DayOfMonth": 6},
        }]
        result = run_t1(
            "req_test",
            {"npc_content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("DayOfMonth" in e for e in result.errors)

    def test_known_condition_tokens_pass(self) -> None:
        """Whitelisted tokens (Season, Day, HasMod, Weather) keep passing."""
        content = {
            "Format": "1.29.0",
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/X",
                    "Entries": {"k": "v"},
                    "When": {"Season": "Spring", "Day": 5},
                },
                {
                    "Action": "Load",
                    "Target": "Data/Y",
                    "FromFile": "y.json",
                    "When": {"HasMod": "Pathoschild.ContentPatcher"},
                },
                {
                    "Action": "EditData",
                    "Target": "Data/Z",
                    "Entries": {"k": "v"},
                    "When": {"Weather": "Sun"},
                },
            ],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors

    def test_prefixed_mod_token_passes(self) -> None:
        """Keys containing ``/`` (``Esca.EMP/...``, ``<ModID>/...``) are
        mod-defined tokens and must be accepted."""
        content = [{
            "Action": "Load",
            "Target": "Data/X",
            "FromFile": "x.json",
            "When": {"Esca.EMP/SpawnMonster": "green slime"},
        }]
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors

    def test_config_schema_field_as_when_token_passes(self) -> None:
        """A ConfigSchema field name is a valid ``When`` key (reference-mod
        pattern: ``RealismMode``). The golden test must keep passing."""
        content = {
            "Format": "2.9.0",
            "ConfigSchema": {
                "RealismMode": {"AllowValues": "true, false", "Default": "true"},
            },
            "Changes": [{
                "Action": "Load",
                "Target": "Data/X",
                "FromFile": "x.json",
                "When": {"RealismMode": "True"},
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors

    def test_dynamic_token_as_when_token_passes(self) -> None:
        """A DynamicTokens name is a valid ``When`` key (reference-mod
        pattern: ``TVSNItemID``)."""
        content = {
            "Format": "2.9.0",
            "DynamicTokens": [
                {"Name": "TVSNItemID", "Value": "%item id (O)298 20"},
            ],
            "Changes": [{
                "Action": "Load",
                "Target": "Data/X",
                "FromFile": "x.json",
                "When": {"TVSNItemID": "%item id (O)298 20"},
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors

    def test_token_with_args_when_key_passes(self) -> None:
        """Token-with-arguments keys (``Random:{{Range:1,20}}``) are valid."""
        content = [{
            "Action": "Load",
            "Target": "Data/X",
            "FromFile": "x.json",
            "When": {"Random:{{Range:1,20}}": "1"},
        }]
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors


class TestEditDataFromFile:
    """``FromFile`` is only valid on Load/EditImage, never on EditData."""

    def test_editdata_with_fromfile_rejected(self) -> None:
        content = {
            "Format": "1.29.0",
            "Changes": [{
                "Action": "EditData",
                "Target": "Data/mail",
                "FromFile": "assets/data/mail.json",
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("'EditData' can't have 'FromFile'" in e for e in result.errors)

    def test_load_with_fromfile_passes(self) -> None:
        """The valid combination (FromFile on Load) keeps passing."""
        content = {
            "Format": "1.29.0",
            "Changes": [
                {"Action": "Load", "Target": "Data/X", "FromFile": "x.json"},
            ],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors


class TestEditMapPosition:
    """``EditMap`` MapTiles entries must carry a non-empty ``Position``."""

    def test_maptiles_missing_position_rejected(self) -> None:
        """The farm_expansion bug: MapTiles used Layer/X/Y instead of Position."""
        content = {
            "Format": "1.29.0",
            "Changes": [{
                "Action": "EditMap",
                "Target": "Maps/Farm",
                "MapTiles": [{"Layer": "Back", "X": 5, "Y": 5}],
            }],
        }
        result = run_t1(
            "req_test",
            {"farm_expansion_content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("missing 'Position'" in e for e in result.errors)

    def test_maptiles_with_position_passes(self) -> None:
        """A tile that carries Position (any non-empty form) keeps passing."""
        content = {
            "Format": "1.29.0",
            "Changes": [{
                "Action": "EditMap",
                "Target": "Maps/Farm",
                "MapTiles": [
                    {"Position": {"X": 5, "Y": 5}, "Layer": "Back"},
                ],
            }],
        }
        result = run_t1(
            "req_test",
            {"content_json_generator": _out_with("content.json", content)},
        )
        assert result.passed is True, result.errors

    def test_legacy_list_root_checked_too(self) -> None:
        """The CP 1.x bare-array root goes through the same per-change checks."""
        content = [{
            "Action": "EditMap",
            "Target": "Maps/Town",
            "MapTiles": [{"Layer": "Buildings"}],
        }]
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("content.json", content)},
        )
        assert result.passed is False
        assert any("missing 'Position'" in e for e in result.errors)
