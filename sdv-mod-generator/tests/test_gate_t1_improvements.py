"""Tests for the v48 → master gate_t1.py improvement port.

Pins the small, focused improvements ported from the discord-ops-hardening
branch's ``docs/_source_gate_t1.py.txt``:

* Type annotation on ``_validate_file`` is now ``dict | list | str``
  (master previously had ``dict | str``, which lied about list inputs).
* Type-disclosure error messages for non-dict/list JSON and TSV content —
  the operator-facing error now names the actual ``type(content).__name__``
  so a generator returning ``42`` or ``None`` is distinguishable from one
  returning ``False`` or ``[]``.
* TSV empty-string detection works: master had a dead-code branch
  (``len(lines) < 1`` is unreachable since ``split`` always yields >= 1
  element) so empty TSVs slipped through T1. The new code checks
  ``stripped`` directly.
* ``config_schema_generator`` no longer crashes with ``TypeError`` when a
  generator emits a non-dict (int / list / None) for ``config.json``; it
  surfaces a precise type-disclosure error in the gate's report instead.
* ``trigger_logic_generator`` now requires a non-empty *dict* (was: any
  truthy value, which let ``[1, 2, 3]`` silently pass as "well-formed").

Reference: docs/_source_gate_t1.py.txt lines 134-218 (``_validate_file``)
and lines 221-352 (``_gen_specific_validation``).
"""
from __future__ import annotations

import pytest

from generators.core import GeneratorOutput
from quality.gate_t1 import run_t1


def _out_with(file_path: str, content) -> GeneratorOutput:
    """Build a single-file GeneratorOutput for a synthetic generator."""
    out = GeneratorOutput()
    out.add_file(file_path, content)
    return out


class TestValidateFileTypeDisclosure:
    """Error messages must disclose the actual Python type of bad JSON content."""

    def test_int_for_json_file_discloses_type(self) -> None:
        """An integer ``42`` for a ``.json`` file surfaces ``int`` in the error."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", 42)},
        )
        assert result.passed is False
        assert any("(got int)" in e and "foo.json" in e for e in result.errors)

    def test_none_for_json_file_discloses_type(self) -> None:
        """A ``None`` for a ``.json`` file surfaces ``NoneType`` in the error."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", None)},
        )
        assert result.passed is False
        assert any("(got NoneType)" in e for e in result.errors)

    def test_bool_for_json_file_discloses_type(self) -> None:
        """``False`` for a ``.json`` file surfaces ``bool`` in the error."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", False)},
        )
        assert result.passed is False
        assert any("(got bool)" in e for e in result.errors)

    def test_string_json_with_int_payload_discloses_parsed_type(self) -> None:
        """A string ``"42"`` (valid JSON but not an object/array) discloses ``int``.

        Goes through the ``json.loads(content)`` branch — the parsed
        Python type (``int``, not ``str``) is what gets disclosed, so
        operators can distinguish "not JSON at all" from "JSON but
        not an object/array".
        """
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", "42")},
        )
        assert result.passed is False
        assert any(
            "parsed but is not a JSON object or array (got int)" in e
            for e in result.errors
        )

    def test_string_json_with_object_payload_passes(self) -> None:
        """A JSON string that parses to a dict passes (e.g. serialized dict)."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", '{"k": "v"}')},
        )
        assert result.passed is True
        assert result.errors == []

    def test_unparseable_string_disclosed_as_not_valid_json(self) -> None:
        """A non-JSON string still surfaces the clear "not valid JSON" message."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("foo.json", "not json at all")},
        )
        assert result.passed is False
        assert any("is not valid JSON" in e for e in result.errors)


class TestValidateFileTsvEmpty:
    """Empty TSV files must be caught by the gate (regression: master dead code)."""

    def test_empty_string_tsv_is_rejected(self) -> None:
        """The exact bug: an empty-string TSV silently passed the master gate."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("data/x.tsv", "")},
        )
        assert result.passed is False
        assert any("data/x.tsv is empty" in e for e in result.errors)

    def test_whitespace_only_tsv_is_rejected(self) -> None:
        """Whitespace-only TSVs are equivalent to empty — must also be rejected."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("data/x.tsv", "   \n\t \n")},
        )
        assert result.passed is False
        assert any("data/x.tsv is empty" in e for e in result.errors)

    def test_single_newline_tsv_is_rejected(self) -> None:
        """A single newline (looks non-empty to the eye) is whitespace-only."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("data/x.tsv", "\n")},
        )
        assert result.passed is False
        assert any("data/x.tsv is empty" in e for e in result.errors)

    def test_nonempty_tsv_pass(self) -> None:
        """Sanity: a real TSV with a single row continues to pass."""
        result = run_t1(
            "req_test",
            {"some_generator": _out_with("data/x.tsv", "col1\tcol2\nval1\tval2\n")},
        )
        # ``_validate_file`` only checks non-empty + string-typed; per-column
        # structure is checked elsewhere (or not at all for non-shop TSVs).
        assert result.passed is True
        assert result.errors == []


class TestConfigSchemaNonDict:
    """``config_schema_generator`` must not crash on non-dict config content."""

    def test_int_config_discloses_type(self) -> None:
        """Integer ``config.json`` returns a precise error instead of crashing."""
        result = run_t1(
            "req_test",
            {"config_schema_generator": _out_with("config.json", 0)},
        )
        assert result.passed is False
        assert any("(got int)" in e and "config.json" in e for e in result.errors)
        # Must not also produce the "missing Enabled" message — the type
        # branch is exclusive of the field-membership branch.
        assert not any("missing 'Enabled' field" in e for e in result.errors)

    def test_none_config_discloses_type(self) -> None:
        """``None`` config returns a precise error instead of crashing."""
        result = run_t1(
            "req_test",
            {"config_schema_generator": _out_with("config.json", None)},
        )
        assert result.passed is False
        assert any("(got NoneType)" in e for e in result.errors)

    def test_list_config_discloses_type(self) -> None:
        """List config returns a precise error instead of ``TypeError``."""
        result = run_t1(
            "req_test",
            {"config_schema_generator": _out_with("config.json", [])},
        )
        assert result.passed is False
        assert any("(got list)" in e for e in result.errors)


class TestTriggerLogicNonDict:
    """``trigger_logic_generator`` must require a non-empty *dict*, not just truthy."""

    def test_list_payload_rejected(self) -> None:
        """A non-empty list (the v48 bug: looks truthy) is now rejected."""
        result = run_t1(
            "req_test",
            {
                "trigger_logic_generator": _out_with(
                    "data/trigger_actions.json",
                    [{"Action": "Mail"}],
                ),
            },
        )
        assert result.passed is False
        assert any("trigger_logic_generator: data/trigger_actions.json" in e for e in result.errors)

    def test_int_truthy_payload_rejected(self) -> None:
        """A truthy non-dict (int 1) is now rejected — was: passed silently."""
        result = run_t1(
            "req_test",
            {
                "trigger_logic_generator": _out_with(
                    "data/trigger_actions.json",
                    1,
                ),
            },
        )
        assert result.passed is False
        assert any("trigger_logic_generator: data/trigger_actions.json" in e for e in result.errors)

    def test_empty_dict_rejected(self) -> None:
        """Empty dict is still rejected (matches the existing contract)."""
        result = run_t1(
            "req_test",
            {
                "trigger_logic_generator": _out_with(
                    "data/trigger_actions.json",
                    {},
                ),
            },
        )
        assert result.passed is False
        assert any("missing or empty" in e for e in result.errors)

    def test_nonempty_dict_passes(self) -> None:
        """Sanity: a real non-empty trigger dict continues to pass."""
        result = run_t1(
            "req_test",
            {
                "trigger_logic_generator": _out_with(
                    "data/trigger_actions.json",
                    {"OnShopOpen": [{"Action": "Mail"}]},
                ),
            },
        )
        assert result.passed is True
        assert result.errors == []
