"""Tests for the Session 2 phase-keyed estimate endpoints.

Companion to the v55 (schemas) + v56 (handlers) ports. Pins the
handler-direct behaviour of ``list_estimates`` and
``get_estimate_for_phase`` against a stub ``app.estimation`` module
(the real module is missing on master — see
``docs/PENDING_SOURCE_BUNDLE.md``).

Mirrors the v56 → v59 split used for the Session 2 endpoints: the
schema-level invariants live in
``test_estimates_response_schemas.py`` (already on master, v55);
this file covers the **handler** behaviour. Same stub pattern as
``test_prompt_estimate_endpoints.py`` but smaller scope — these
two endpoints don't touch the router, only the estimation table
and the ``estimate_seconds_for_phase`` helper.

Covers:
- ``list_estimates`` (GET /v1/estimates):
  - happy path: rows sorted by phase id, count mirrors len(rows),
    default_seconds echoes the stub constant
  - response is ``EstimatesResponse`` instance (pins response_model)
  - lazy cache: second call returns the same object (the module-level
    ``_ESTIMATES_CACHE`` is populated on the first call and reused)
  - empty phase table (edge case): empty list, count=0, default still
    echoes the stub
- ``get_estimate_for_phase`` (GET /v1/estimates/{phase}):
  - matched phase: seconds == phase-specific value, matched=True
  - unknown phase: seconds == default_seconds, matched=False, phase
    echoed back
  - whitespace-only phase (the defensive ``/v1/estimates/%20%20``
    path): treated as unknown, matched=False, phase == ""
  - phase is stripped of leading/trailing whitespace before lookup
  - response is ``PhaseEstimateResponse`` instance
- ``_build_estimates_response`` cache invalidation: between tests the
  module-level ``_ESTIMATES_CACHE`` must NOT carry state from a prior
  test (handled by the autouse fixture below).
"""
from __future__ import annotations

import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Autouse fixture: reset the module-level _ESTIMATES_CACHE between tests.
# ``_build_estimates_response`` populates the cache on first call and
# returns it on every subsequent call. Without this reset, the second
# test's stub values would be ignored because the cache from the first
# test would still be in place. Mirrors the same teardown pattern used
# by other module-level-cache fixtures in this repo.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_estimates_cache():
    """Reset ``app.api.routes._ESTIMATES_CACHE`` before and after each test."""
    from app.api import routes

    routes._ESTIMATES_CACHE = None
    yield
    routes._ESTIMATES_CACHE = None


# ---------------------------------------------------------------------------
# Stub app.estimation so the deferred imports in ``_build_estimates_response``
# and ``get_estimate_for_phase`` resolve even when the real module is
# missing on master. Same shape as the v58 fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_app_estimation(monkeypatch: pytest.MonkeyPatch):
    """Inject a stub ``app.estimation`` module into sys.modules."""
    module = types.ModuleType("app.estimation")
    # Two known phases so matched-vs-unknown paths are both reachable.
    phase_seconds = {"shop_channel": 30, "weather_event": 45}
    default_seconds = 90

    def estimate_seconds_for_phase(phase: str | None) -> int:
        if phase is None or phase == "":
            return default_seconds
        if phase in phase_seconds:
            return phase_seconds[phase]
        return default_seconds

    # Use setattr to keep Pyright's ModuleType attribute-access warning
    # quiet — see the matching rationale in test_prompt_estimate_endpoints.
    setattr(module, "_PHASE_SECONDS", phase_seconds)  # noqa: SLF001
    setattr(module, "_DEFAULT_SECONDS", default_seconds)  # noqa: SLF001
    setattr(module, "estimate_seconds_for_phase", estimate_seconds_for_phase)

    monkeypatch.setitem(sys.modules, "app.estimation", module)
    return module


# ---------------------------------------------------------------------------
# list_estimates tests.
# ---------------------------------------------------------------------------


class TestListEstimatesEndpoint:
    """Tests for ``list_estimates`` (GET /v1/estimates)."""

    async def test_happy_path_rows_sorted_by_phase(
        self, stub_app_estimation,
    ):
        """The handler returns one ``PhaseEstimate`` per known phase,
        sorted by phase id (lexicographic). ``count`` mirrors
        ``len(estimates)`` and ``default_seconds`` echoes the stub
        constant. The endpoint is read-only — no router / DB / Redis.
        """
        from app.api.routes import list_estimates

        result = await list_estimates()

        # Stub has 2 phases; lexicographic sort: shop_channel < weather_event.
        assert result.count == 2
        assert len(result.estimates) == 2
        assert result.default_seconds == 90
        assert [row.phase for row in result.estimates] == [
            "shop_channel",
            "weather_event",
        ]
        assert result.estimates[0].seconds == 30
        assert result.estimates[1].seconds == 45

    async def test_response_is_estimates_response_instance(
        self, stub_app_estimation,
    ):
        """The handler returns an ``EstimatesResponse`` (not a raw
        dict) so OpenAPI / JSON serialization goes through Pydantic.
        Pins the ``response_model`` contract.
        """
        from app.api.routes import list_estimates
        from app.api.schemas import EstimatesResponse

        result = await list_estimates()

        assert isinstance(result, EstimatesResponse)

    async def test_lazy_cache_returns_same_object(
        self, stub_app_estimation,
    ):
        """The first call populates ``_ESTIMATES_CACHE``; the second
        call returns the SAME object (identity, not just equality).
        This is the documented CPU-saving optimization for high-
        frequency polling callers.
        """
        from app.api.routes import list_estimates

        first = await list_estimates()
        second = await list_estimates()

        assert first is second

    async def test_empty_phase_table_returns_empty_rows(
        self, monkeypatch, stub_app_estimation,
    ):
        """Edge case: a phase table with zero rows produces an
        ``EstimatesResponse`` with ``estimates == []`` and
        ``count == 0`` but ``default_seconds`` still echoes the
        stub constant (the default is independent of the per-phase
        rows). The handler must not crash on an empty table.
        """
        from app.api.routes import list_estimates

        # Re-stub with an empty phase table — keeps ``estimate_seconds_for_phase``
        # but clears _PHASE_SECONDS.
        empty_module = types.ModuleType("app.estimation")
        setattr(empty_module, "_PHASE_SECONDS", {})  # noqa: SLF001
        setattr(empty_module, "_DEFAULT_SECONDS", 90)  # noqa: SLF001

        def empty_estimator(phase):  # pragma: no cover - trivial
            return 90

        setattr(empty_module, "estimate_seconds_for_phase", empty_estimator)
        monkeypatch.setitem(sys.modules, "app.estimation", empty_module)

        result = await list_estimates()

        assert result.count == 0
        assert result.estimates == []
        assert result.default_seconds == 90


# ---------------------------------------------------------------------------
# get_estimate_for_phase tests.
# ---------------------------------------------------------------------------


class TestGetEstimateForPhaseEndpoint:
    """Tests for ``get_estimate_for_phase`` (GET /v1/estimates/{phase})."""

    async def test_matched_phase_returns_phase_specific_seconds(
        self, stub_app_estimation,
    ):
        """A phase present in ``_PHASE_SECONDS`` returns its
        phase-specific seconds and ``matched=True``.
        """
        from app.api.routes import get_estimate_for_phase

        result = await get_estimate_for_phase("shop_channel")

        assert result.phase == "shop_channel"
        assert result.seconds == 30
        assert result.default_seconds == 90
        assert result.matched is True

    async def test_unknown_phase_returns_default_seconds(
        self, stub_app_estimation,
    ):
        """An unknown phase returns ``default_seconds`` and
        ``matched=False``, but still echoes the requested phase id
        back so the client can render "unknown phase — using default".
        """
        from app.api.routes import get_estimate_for_phase

        result = await get_estimate_for_phase("mystery_phase")

        assert result.phase == "mystery_phase"
        assert result.seconds == 90  # == default
        assert result.default_seconds == 90
        assert result.matched is False

    async def test_whitespace_only_phase_treated_as_unknown(
        self, stub_app_estimation,
    ):
        """FastAPI's path param rejects the empty string at the
        routing layer, but a whitespace-only phase (e.g.
        ``/v1/estimates/%20%20``) slips through. The handler's
        defensive strip collapses it to ``""`` and the lookup
        falls through to the default — so the response shape is
        consistent with the unknown-phase case.
        """
        from app.api.routes import get_estimate_for_phase

        result = await get_estimate_for_phase("   ")

        # phase is collapsed to "" (the defensive strip) and the
        # response is the same shape as an unknown-phase lookup.
        assert result.phase == ""
        assert result.seconds == 90
        assert result.default_seconds == 90
        assert result.matched is False

    async def test_phase_is_stripped_before_lookup(
        self, stub_app_estimation,
    ):
        """Leading/trailing whitespace around a known phase is
        stripped before the table lookup, so the matched path
        still fires for ``"  shop_channel  "``. The echoed
        ``phase`` field is the STRIPPED value (not the raw input).
        """
        from app.api.routes import get_estimate_for_phase

        result = await get_estimate_for_phase("  shop_channel  ")

        assert result.phase == "shop_channel"
        assert result.seconds == 30
        assert result.matched is True

    async def test_response_is_phase_estimate_response_instance(
        self, stub_app_estimation,
    ):
        """The handler returns a ``PhaseEstimateResponse`` (not a
        raw dict) so OpenAPI / JSON serialization goes through
        Pydantic. Pins the ``response_model`` contract.
        """
        from app.api.routes import get_estimate_for_phase
        from app.api.schemas import PhaseEstimateResponse

        result = await get_estimate_for_phase("shop_channel")

        assert isinstance(result, PhaseEstimateResponse)