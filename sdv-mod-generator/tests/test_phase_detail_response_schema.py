"""Schema-level tests for the v60 ``PhaseDetailResponse`` Pydantic model.

Companion to the v60 schema port — pins the Pydantic contract that
the ``GET /v1/mods/phases/{phase_id}`` handler emits. Schema-only
(no TestClient, no handler import, no ``app.estimation`` import)
because the route handlers depend on the missing ``app.estimation``
module — see ``docs/PENDING_SOURCE_BUNDLE.md``.

Mirrors v54 (``test_estimates_response_schemas.py``) and v55
(``test_prompt_estimate_response_schemas.py``). Splits from v61 the
same way those split from their handler-direct tests: this round
pins schema invariants, v61 pins handler-direct contract pins.

Four invariants pinned here:

  1. Matched-phase happy path — all 9 fields populated; the
     ``default_factory=list`` on ``execution_order`` yields a fresh
     list per instance (no shared mutable default).
  2. Unknown-phase graceful shape — ``matched=False``, owning-pack
     fields default to empty strings (caller doesn't have to pass
     them).
  3. Numeric guards — ``generator_count >= 0`` (0 ok, -1 rejected),
     ``estimated_seconds >= 1`` and ``default_seconds >= 1``
     (rationale: a stale cache must not leak a 0 or a negative
     estimate to a UI).
  4. Required fields — ``phase`` / ``matched`` / ``generator_count``
     / ``estimated_seconds`` / ``default_seconds`` have no defaults;
     omitting any one is a ``ValidationError``.

Not pinned here (intentional, deferred): empty-string-vs-None
coercion for owning-pack fields (the schema uses ``default=""``
explicitly), long-string acceptance (no ``max_length`` set), and
JSON round-trip via ``model_dump_json`` (Pydantic's own test suite
pins that).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import PhaseDetailResponse


class TestPhaseDetailResponseMatched:
    """``PhaseDetailResponse`` happy path — a phase that matched a pack."""

    def test_full_envelope_round_trip(self) -> None:
        # All 9 fields populated. Verifies the schema accepts the
        # full field set and round-trips each value unchanged.
        r = PhaseDetailResponse(
            phase="shop_channel",
            matched=True,
            game_id="stardew_valley",
            display_name="Stardew Valley",
            mod_format="ContentPatcher",
            generator_count=2,
            execution_order=["texture", "shop_channel"],
            estimated_seconds=30,
            default_seconds=90,
        )
        assert r.phase == "shop_channel"
        assert r.matched is True
        assert r.game_id == "stardew_valley"
        assert r.display_name == "Stardew Valley"
        assert r.mod_format == "ContentPatcher"
        assert r.generator_count == 2
        assert r.execution_order == ["texture", "shop_channel"]
        assert r.estimated_seconds == 30
        assert r.default_seconds == 90

    def test_execution_order_defaults_to_empty_list(self) -> None:
        # ``execution_order`` uses ``default_factory=list`` — the
        # caller must not have to pass it for the unknown-phase
        # shape. Pin that omitting it yields a fresh empty list
        # per instance (no shared mutable default).
        r1 = PhaseDetailResponse(
            phase="shop_channel",
            matched=True,
            generator_count=0,
            estimated_seconds=30,
            default_seconds=90,
        )
        assert r1.execution_order == []
        # Mutating the instance's list must not bleed into another
        # instance — default_factory isolation.
        r1.execution_order.append("leaked")
        r2 = PhaseDetailResponse(
            phase="shop_channel",
            matched=True,
            generator_count=0,
            estimated_seconds=30,
            default_seconds=90,
        )
        assert r2.execution_order == []


class TestPhaseDetailResponseUnknown:
    """``PhaseDetailResponse`` graceful shape — phase not in any pack."""

    def test_minimal_unknown_phase_round_trip(self) -> None:
        # Caller only needs to supply the required fields —
        # ``game_id`` / ``display_name`` / ``mod_format`` default to
        # empty strings, ``execution_order`` defaults to ``[]``,
        # ``generator_count`` defaults to 0, ``estimated_seconds``
        # and ``default_seconds`` fall through to the default 90.
        r = PhaseDetailResponse(
            phase="not_a_real_phase",
            matched=False,
            generator_count=0,
            estimated_seconds=90,
            default_seconds=90,
        )
        assert r.phase == "not_a_real_phase"
        assert r.matched is False
        assert r.game_id == ""
        assert r.display_name == ""
        assert r.mod_format == ""
        assert r.execution_order == []
        assert r.generator_count == 0
        assert r.estimated_seconds == 90
        assert r.default_seconds == 90


class TestPhaseDetailResponseNumericGuards:
    """Pydantic ``Field`` constraints on the three numeric fields."""

    def test_generator_count_must_be_ge_zero(self) -> None:
        # Boundary: 0 is ok (matched=False shape).
        PhaseDetailResponse(
            phase="x", matched=False, generator_count=0,
            estimated_seconds=90, default_seconds=90,
        )
        # Negative counts are nonsense and must be rejected.
        with pytest.raises(ValidationError):
            PhaseDetailResponse(
                phase="x", matched=False, generator_count=-1,
                estimated_seconds=90, default_seconds=90,
            )

    def test_seconds_fields_must_be_ge_one(self) -> None:
        # Boundary: 1 is ok for both fields.
        PhaseDetailResponse(
            phase="x", matched=True, generator_count=1,
            estimated_seconds=1, default_seconds=1,
        )
        # 0 / negative must be rejected on both fields — a stale
        # cache must not leak a 0 or a negative estimate to a UI.
        for bad in (0, -30):
            with pytest.raises(ValidationError):
                PhaseDetailResponse(
                    phase="x", matched=True, generator_count=1,
                    estimated_seconds=bad, default_seconds=90,
                )
            with pytest.raises(ValidationError):
                PhaseDetailResponse(
                    phase="x", matched=True, generator_count=1,
                    estimated_seconds=90, default_seconds=bad,
                )


class TestPhaseDetailResponseRequiredFields:
    """Fields without defaults — omitting any one is a ``ValidationError``."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(
                {"matched": True, "generator_count": 1,
                 "estimated_seconds": 30, "default_seconds": 90},
                id="missing_phase",
            ),
            pytest.param(
                {"phase": "x", "generator_count": 1,
                 "estimated_seconds": 30, "default_seconds": 90},
                id="missing_matched",
            ),
            pytest.param(
                {"phase": "x", "matched": True,
                 "estimated_seconds": 30, "default_seconds": 90},
                id="missing_generator_count",
            ),
            pytest.param(
                {"phase": "x", "matched": True,
                 "generator_count": 1, "default_seconds": 90},
                id="missing_estimated_seconds",
            ),
            pytest.param(
                {"phase": "x", "matched": True,
                 "generator_count": 1, "estimated_seconds": 30},
                id="missing_default_seconds",
            ),
        ],
    )
    def test_missing_required_field_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            PhaseDetailResponse(**kwargs)  # type: ignore[arg-type]