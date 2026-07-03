"""Tests for the gate_t1 ``manifest_generator`` non-dict guard.

Pins the v48 style hardening ported from the discord-ops-hardening
branch's ``docs/_source_gate_t1.py.txt`` (lines 271-283): a generator
that emits a non-dict (int / list / None) for ``manifest.json`` no
longer crashes the gate with a raw ``TypeError`` from the
``field_name not in manifest`` membership test — instead the gate
collects a precise type-disclosure error and continues to the
other arms.

Mirrors the structure of ``TestConfigSchemaNonDict`` in
``test_gate_t1_improvements.py`` (the same v48 pattern was already
applied to ``config_schema_generator`` there).

Reference: docs/_source_gate_t1.py.txt lines 221-298
(``_gen_specific_validation``, ``manifest_generator`` arm).
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


class TestManifestGeneratorNonDict:
    """``manifest_generator`` must not crash on non-dict manifest content."""

    def test_int_manifest_discloses_type(self) -> None:
        """Integer ``manifest.json`` returns a precise error instead of crashing.

        Without the guard this raises ``TypeError: argument of type 'int'
        is not iterable`` from the ``"Format" not in manifest`` membership
        test, which would short-circuit the gate and hide every other error.
        """
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", 0)},
        )
        assert result.passed is False
        assert any(
            "(got int)" in e and "manifest.json" in e for e in result.errors
        )
        # Must NOT also produce the "missing required field" messages —
        # the type branch is exclusive of the field-membership branch.
        assert not any("missing required field" in e for e in result.errors)

    def test_none_manifest_discloses_type(self) -> None:
        """``None`` manifest returns a precise error instead of crashing."""
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", None)},
        )
        assert result.passed is False
        assert any(
            "(got NoneType)" in e and "manifest.json" in e for e in result.errors
        )

    def test_list_manifest_discloses_type(self) -> None:
        """List manifest returns a precise error instead of ``TypeError``.

        Regression: before the guard, ``"Format" not in [...]`` raised
        ``TypeError: unhashable type: 'list'``. The membership test is
        invalid on a list, but the gate should never let that escape.
        """
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", [])},
        )
        assert result.passed is False
        assert any(
            "(got list)" in e and "manifest.json" in e for e in result.errors
        )

    def test_bool_manifest_discloses_type(self) -> None:
        """``True`` / ``False`` manifest returns a precise error.

        ``False`` would have silently passed the master code's
        ``if not manifest`` check at line 47 of ``_validate_generator_output``
        before reaching this arm (since ``bool`` is a subtype of ``int``
        and passes through ``output.files.get``), but the
        ``field_name not in manifest`` test would still crash because
        ``False not in <missing>`` is invalid on a bool. The guard makes
        the failure mode operator-friendly instead of a gate crash.
        """
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", False)},
        )
        assert result.passed is False
        assert any(
            "(got bool)" in e and "manifest.json" in e for e in result.errors
        )


class TestManifestGeneratorStillCorrect:
    """The guard must not regress the well-formed manifest path."""

    def test_well_formed_manifest_passes(self, sample_manifest: dict) -> None:
        """Sanity: a complete, well-formed manifest continues to pass.

        Uses the conftest fixture to avoid duplicating the canonical
        Content Patcher shape across the test suite.
        """
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", sample_manifest)},
        )
        assert result.passed is True
        assert result.errors == []

    def test_missing_required_fields_still_reported(
        self,
        malformed_manifest: dict,
    ) -> None:
        """The field-membership branch must still run on dict inputs.

        Regression: the guard wraps the membership test in ``else``,
        so dict inputs take the original ``required`` loop path.
        Verifies that path still produces the expected per-field errors.
        """
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", malformed_manifest)},
        )
        assert result.passed is False
        # The conftest's malformed_manifest omits ``UniqueID``,
        # ``Version``, and ``ContentPackFor``.
        assert any(
            "missing required field 'UniqueID'" in e for e in result.errors
        )
        assert any(
            "missing required field 'Version'" in e for e in result.errors
        )

    def test_contentpackfor_missing_uniqueid_reported(self) -> None:
        """``ContentPackFor`` dict without ``UniqueID`` still reports the error."""
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "tv_shopping",
            "Name": "TV Shopping",
            "Version": "1.0.0",
            "ContentPackFor": {},  # missing UniqueID
        }
        result = run_t1(
            "req_test",
            {"manifest_generator": _out_with("manifest.json", manifest)},
        )
        assert result.passed is False
        assert any(
            "ContentPackFor.UniqueID missing" in e for e in result.errors
        )
