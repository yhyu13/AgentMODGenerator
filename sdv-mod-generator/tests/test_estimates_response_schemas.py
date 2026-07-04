"""Schema-level tests for the Session 2 estimate response schemas.

Companion to the v54 schema port — pins the Pydantic contract that the
``GET /v1/estimates`` and ``GET /v1/estimates/{phase}`` routes (next
round, v55) will emit. Schema-only (no TestClient) because the route
handlers depend on ``app.estimation`` which is not on master yet —
see ``docs/PENDING_SOURCE_BUNDLE.md``. Mirrors the v33 (schema) → v34
(handler + handler tests) split used for Session 5 endpoint 3/4
(``/v1/feature_flags/history``).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    EstimatesResponse,
    PhaseEstimate,
    PhaseEstimateResponse,
)


class TestPhaseEstimate:
    """``PhaseEstimate`` is one row of the canonical phase→seconds table."""

    def test_minimal_round_trip(self) -> None:
        e = PhaseEstimate(phase="shop_channel", seconds=30)
        assert e.phase == "shop_channel"
        assert e.seconds == 30

    def test_seconds_must_be_ge_1(self) -> None:
        # ``seconds`` is constrained to >=1 — the canonical table
        # has no zero/negative entries. Pin both edges.
        PhaseEstimate(phase="weather_event", seconds=1)  # boundary: ok
        with pytest.raises(ValidationError):
            PhaseEstimate(phase="weather_event", seconds=0)
        with pytest.raises(ValidationError):
            PhaseEstimate(phase="weather_event", seconds=-5)

    def test_missing_required_field_raises(self) -> None:
        # Both fields required — no defaults.
        with pytest.raises(ValidationError):
            PhaseEstimate(seconds=30)  # type: ignore[call-arg]


class TestEstimatesResponse:
    """``EstimatesResponse`` is the ``/v1/estimates`` envelope."""

    def test_empty_envelope(self) -> None:
        r = EstimatesResponse(estimates=[], default_seconds=90, count=0)
        assert r.estimates == []
        assert r.default_seconds == 90
        assert r.count == 0

    def test_default_seconds_must_be_ge_1(self) -> None:
        # Same rationale as ``PhaseEstimate.seconds`` — the fallback
        # estimate must be positive so a stale cache can't leak a 0.
        EstimatesResponse(estimates=[], default_seconds=1, count=0)  # boundary: ok
        with pytest.raises(ValidationError):
            EstimatesResponse(estimates=[], default_seconds=0, count=0)


class TestPhaseEstimateResponse:
    """``PhaseEstimateResponse`` is the single-phase lookup envelope."""

    def test_matched_true_round_trip(self) -> None:
        # The canonical-table hit case — ``seconds`` is the
        # phase-specific estimate, ``matched`` is True, ``phase``
        # echoes back verbatim.
        r = PhaseEstimateResponse(
            phase="shop_channel",
            seconds=30,
            default_seconds=90,
            matched=True,
        )
        assert r.phase == "shop_channel"
        assert r.seconds == 30
        assert r.default_seconds == 90
        assert r.matched is True

    def test_matched_false_round_trip(self) -> None:
        # The graceful-degrade case — the requested phase is NOT in
        # the canonical table, so ``seconds == default_seconds`` and
        # ``matched`` is False. The phase id is still echoed back.
        r = PhaseEstimateResponse(
            phase="unknown_phase_xyz",
            seconds=90,
            default_seconds=90,
            matched=False,
        )
        assert r.phase == "unknown_phase_xyz"
        assert r.seconds == 90
        assert r.matched is False