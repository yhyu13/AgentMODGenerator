"""Tests for ``orchestrator.state.PipelineState`` dataclass invariants.

Round v119: pins ``orchestrator/state.py:8-53`` — single source of
truth through all pipeline nodes. Pure dataclass, no I/O. Pins
required fields, defaults match source, mutable default containers
are independent per-instance (``field(default_factory)``), ``status``
Literal accepts all eight declared states, ``zip_key`` defaults to
``None`` (not ``""``/``{}``).

Pure test infrastructure — does NOT modify ``orchestrator/state.py``
or ``orchestrator/pipeline.py``. The orphan ``.pyc`` at
``tests/__pycache__/test_pipeline_state_invariants.cpython-311-pytest-9.0.3.pyc``
exists on master but the live ``.py`` source is missing — same
pattern that v68 / v69 / v117 / v118 fixed. v119 closes the
orphan-``.pyc`` series.
"""
from __future__ import annotations

import pytest

from generators.core import GeneratorOutput


class TestPipelineStateRequiredFields:
    """The three positional str fields must be supplied at construction."""

    def test_minimal_construction_echoes_all_three(self):
        from orchestrator.state import PipelineState
        state = PipelineState(
            request_id="req-1", user_id="user-1", prompt="make a TV shop",
        )
        assert state.request_id == "req-1"
        assert state.user_id == "user-1"
        assert state.prompt == "make a TV shop"

    @pytest.mark.parametrize("missing_field", ["request_id", "user_id", "prompt"])
    def test_missing_required_field_raises_type_error(self, missing_field):
        """Pin field names — a future rename surfaces here."""
        from orchestrator.state import PipelineState
        kwargs = {"request_id": "r", "user_id": "u", "prompt": "p"}
        kwargs.pop(missing_field)
        with pytest.raises(TypeError) as exc_info:
            PipelineState(**kwargs)  # type: ignore[call-arg]
        assert missing_field in str(exc_info.value)


class TestPipelineStateDefaults:
    """Default values must match the source dataclass declaration."""

    def test_defaults_match_source(self):
        """One test asserts every default in one shot — any drift in
        any default field surfaces with one failure rather than 12."""
        from orchestrator.state import PipelineState
        state = PipelineState(request_id="r", user_id="u", prompt="p")
        # Defaults match ``orchestrator/state.py:8-53`` exactly.
        assert state.game == "stardew_valley"
        assert state.phase == ""  # empty string (not None)
        assert state.zip_key is None  # None (not ""/{})
        assert state.t1_passed is True
        assert state.t2_passed is True
        assert state.t2_available is True
        assert state.t2_score == 0
        assert state.t2_iterations == 0
        assert state.t2_panel_passed_count == 0
        assert state.max_t2_iterations == 0  # v109 contract
        assert state.t2_feedback == ""
        assert state.status == "pending"

    def test_mutable_defaults_are_empty_collections(self):
        """Every mutable default factory starts empty (not None, not
        shared) — downstream code can ``state.generators.append(...)``
        without a None guard."""
        from orchestrator.state import PipelineState
        state = PipelineState(request_id="r", user_id="u", prompt="p")
        assert state.generators == []
        assert state.hint == {}
        assert state.outputs == {}
        assert state.errors == []
        assert state.generators_failed == []
        assert state.generators_succeeded == []
        assert state.t2_judge_results == []


class TestPipelineStateMutableDefaultsAreIndependent:
    """Per-instance independence — classic mutable-default fix via
    ``field(default_factory=...)``. Sharing would leak one request's
    data into another (security-sensitive for ``t2_judge_results``
    and ``outputs``)."""

    def _two_states(self):
        from orchestrator.state import PipelineState
        return (
            PipelineState(request_id="r1", user_id="u1", prompt="p1"),
            PipelineState(request_id="r2", user_id="u2", prompt="p2"),
        )

    @pytest.mark.parametrize(
        "attr,mutator",
        [
            ("generators", lambda s: s.append("shop_channel")),
            ("hint", lambda s: s.__setitem__("phase", "shop_channel")),
            ("outputs", lambda s: s.__setitem__("shop_channel", GeneratorOutput())),
            ("errors", lambda s: s.append("err")),
            ("generators_failed", lambda s: s.append("texture")),
            ("generators_succeeded", lambda s: s.append("shop_channel")),
            ("t2_judge_results", lambda s: s.append({"judge": "a", "score": 8})),
        ],
    )
    def test_per_instance_independence(self, attr, mutator):
        s1, s2 = self._two_states()
        mutator(getattr(s1, attr))
        assert getattr(s2, attr) in ([], {})


class TestPipelineStateStatusLiteral:
    """All 8 status Literal values accepted; runtime does NOT enforce
    Literal (mypy hint only) — pinning this guards against a future
    refactor that adds a ``__post_init__`` validator without
    updating tests."""

    @pytest.mark.parametrize(
        "status_value",
        [
            "pending", "routing", "generating", "t1_gating",
            "t2_gating", "packaging", "done", "failed",
        ],
    )
    def test_each_literal_value_is_accepted(self, status_value):
        from orchestrator.state import PipelineState
        state = PipelineState(
            request_id="r", user_id="u", prompt="p", status=status_value,
        )
        assert state.status == status_value

    def test_arbitrary_string_is_accepted_at_runtime(self):
        """Literal is typing-only — runtime accepts any string."""
        from orchestrator.state import PipelineState
        # ``# type: ignore[arg-type]`` suppresses the mypy/pyright
        # complaint on the literal-vs-Literal mismatch; the test IS
        # verifying runtime does NOT enforce the Literal.
        state = PipelineState(  # type: ignore[arg-type]
            request_id="r", user_id="u", prompt="p",
            status="experimental_state",
        )
        assert state.status == "experimental_state"


class TestPipelineStateExplicitOverride:
    """Every defaulted field settable — positive control for default
    tests (a future field rename fails both)."""

    def test_all_defaulted_fields_round_trip(self):
        from orchestrator.state import PipelineState
        outputs = {"shop_channel": GeneratorOutput()}
        judge = {"judge": "a", "score": 7}
        state = PipelineState(
            request_id="req-x", user_id="user-x", prompt="prompt-x",
            game="stardew_valley", phase="shop_channel",
            generators=["shop_channel"], hint={"phase": "shop_channel"},
            outputs=outputs, errors=["err-1"],
            generators_failed=["texture"], generators_succeeded=["shop_channel"],
            zip_key="mods/req-x.zip",
            t1_passed=False, t2_passed=False, t2_available=False,
            t2_score=7, t2_feedback="needs better sprite",
            t2_iterations=2, max_t2_iterations=2,
            t2_judge_results=[judge], t2_panel_passed_count=1, status="done",
        )
        # Spot-check (full defaults test above covers every default).
        assert state.outputs is outputs  # exact instance preserved
        assert state.t1_passed is False
        assert state.t2_iterations == 2
        assert state.t2_judge_results == [judge]
        assert state.status == "done"


class TestPipelineStateGeneratorOutputContract:
    """``outputs`` dict stores ``GeneratorOutput`` values; ``t2_judge_results``
    round-trip dicts unchanged so the T2 panel node can append
    without losing shape."""

    def test_outputs_accepts_generator_output_round_trip(self):
        from orchestrator.state import PipelineState
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0"})
        out.add_asset("assets/sprite.png")
        state = PipelineState(
            request_id="r", user_id="u", prompt="p",
            outputs={"shop_channel": out},
        )
        assert state.outputs["shop_channel"] is out
        assert state.outputs["shop_channel"].files == {"manifest.json": {"Format": "1.29.0"}}
        assert state.outputs["shop_channel"].assets == ["assets/sprite.png"]

    def test_t2_judge_results_dict_round_trips(self):
        from orchestrator.state import PipelineState
        judge = {"judge": "judge_a", "score": 8, "passed": True, "feedback": "looks good"}
        state = PipelineState(
            request_id="r", user_id="u", prompt="p",
            t2_judge_results=[judge], t2_panel_passed_count=1,
        )
        assert state.t2_judge_results == [judge] and state.t2_panel_passed_count == 1