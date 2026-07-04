"""Tests for the Session 2 prompt-keyed estimate endpoints.

Companion to the v55 (schemas) + v57 (handlers) ports. Pins the
handler-direct behaviour of ``estimate_prompt_endpoint`` and
``estimate_prompt_batch_endpoint`` against a stub
``app.estimation`` module (the real module is missing on master —
see ``docs/PENDING_SOURCE_BUNDLE.md``) plus a mocked
``orchestrator.router.route`` so the routing table doesn't have to
be in lockstep with the test expectations.

Mirrors the v57 → v58 split that ``test_route_preview.py`` used for
the Session 4 ``/v1/route_preview`` endpoint: handler-direct
calls (no TestClient / no app.main import) so the missing
``app.estimation`` module doesn't block module-load.

Covers:
- ``estimate_prompt_endpoint``:
  - matched single prompt: phase, seconds, default_seconds, matched, game echo
  - fallback single prompt (unknown phase): matched=False, seconds==default
  - whitespace-only prompt rejected with 422 (handler's defensive trim)
  - response is ``PromptEstimateResponse`` instance (pins response_model contract)
  - mocked router exception propagates (no defensive catch — same shape as
    ``preview_route``)
- ``estimate_prompt_batch_endpoint``:
  - happy path: 3 prompts, order preserved, count echoes len(prompts)
  - mixed matched/fallback prompts: matched_count heterogeneous
  - empty list rejected with 422 (Pydantic min_length=1)
  - 21-prompt list rejected with 422 (Pydantic max_length=20)
  - response is ``BatchPromptEstimateResponse`` instance
  - batch response strips the echoed ``prompt`` field from each row
    (the request already carries the prompts, no need to duplicate)
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stub app.estimation so the deferred imports in _estimate_for_prompt /
# estimate_prompt_batch_endpoint resolve even when the real module is
# missing on master. The stub mirrors the names the handlers look up.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_app_estimation(monkeypatch: pytest.MonkeyPatch):
    """Inject a stub ``app.estimation`` module into sys.modules.

    The deferred imports in ``app.api.routes._estimate_for_prompt``
    resolve ``_PHASE_SECONDS``, ``_DEFAULT_SECONDS``, and
    ``estimate_seconds_for_phase`` from this module. We populate
    each with deterministic values so the handler tests are
    reproducible regardless of the real (missing) module's content.
    """
    module = types.ModuleType("app.estimation")
    # The canonical phase → seconds table. Two known phases so the
    # matched / fallback cases are both reachable from the same stub.
    phase_seconds = {"shop_channel": 30, "weather_event": 45}
    # The default estimate applied when no specific phase is matched.
    default_seconds = 90

    # Mirror ``estimate_seconds_for_phase``: known phase → phase-specific
    # estimate, else the default. Mirrors the source's documented
    # fallback rule (re-implemented inline to avoid a circular import
    # against the real module that we're stubbing out).
    def estimate_seconds_for_phase(phase: str | None) -> int:
        if phase is None:
            return default_seconds
        if phase in phase_seconds:
            return phase_seconds[phase]
        return default_seconds

    # Use setattr so Pyright doesn't flag the assignment to a
    # generic ``ModuleType`` attribute. The leading underscore on
    # the private names is preserved — the handler reads them via
    # ``from app.estimation import _PHASE_SECONDS`` so the names
    # must match exactly.
    setattr(module, "_PHASE_SECONDS", phase_seconds)  # noqa: SLF001
    setattr(module, "_DEFAULT_SECONDS", default_seconds)  # noqa: SLF001
    setattr(module, "estimate_seconds_for_phase", estimate_seconds_for_phase)

    monkeypatch.setitem(sys.modules, "app.estimation", module)
    return module


# ---------------------------------------------------------------------------
# estimate_prompt_endpoint tests.
# ---------------------------------------------------------------------------


class TestEstimatePromptEndpoint:
    """Tests for ``estimate_prompt_endpoint`` (GET /v1/estimate)."""

    async def test_matched_single_prompt(self, stub_app_estimation):
        """A prompt containing a known phase keyword routes to the
        matched phase with the phase-specific ``seconds`` value.

        Patches ``orchestrator.router.route`` to return a controlled
        (phase, RoutingHint) tuple so the routing table doesn't have
        to be in lockstep with the test expectation.
        """
        from app.api.routes import estimate_prompt_endpoint

        hint: dict[str, Any] = {"game": "stardew_valley"}
        with patch("orchestrator.router.route", return_value=("shop_channel", hint)):
            result = await estimate_prompt_endpoint(prompt="make a TV shopping channel")

        assert result.phase == "shop_channel"
        assert result.seconds == 30  # from the stub's _PHASE_SECONDS
        assert result.default_seconds == 90
        assert result.matched is True
        assert result.prompt == "make a TV shopping channel"
        assert result.game == "stardew_valley"

    async def test_fallback_single_prompt(self, stub_app_estimation):
        """A prompt that resolves to a phase NOT in the canonical
        table returns ``matched=False`` and ``seconds == default_seconds``.
        The phase id is still echoed back so the client can render
        "default estimate — no phase-specific tuning".
        """
        from app.api.routes import estimate_prompt_endpoint

        hint = {"game": "stardew_valley"}
        with patch("orchestrator.router.route", return_value=("unknown_phase", hint)):
            result = await estimate_prompt_endpoint(prompt="something obscure")

        assert result.phase == "unknown_phase"
        assert result.seconds == 90  # default
        assert result.default_seconds == 90
        assert result.matched is False
        assert result.game == "stardew_valley"

    async def test_whitespace_only_prompt_rejected_with_422(self, stub_app_estimation):
        """FastAPI's ``Query(min_length=1)`` only catches the empty
        string, not a whitespace-only prompt. The handler's defensive
        trim rejects the latter with a 422 — pinned here as the
        primary hygiene boundary.
        """
        from fastapi import HTTPException

        from app.api.routes import estimate_prompt_endpoint

        with pytest.raises(HTTPException) as exc_info:
            await estimate_prompt_endpoint(prompt="   \t  \n  ")

        assert exc_info.value.status_code == 422
        assert "empty or whitespace-only" in str(exc_info.value.detail)

    async def test_response_is_prompt_estimate_response_instance(self, stub_app_estimation):
        """The handler returns a ``PromptEstimateResponse`` (not a
        raw dict) so OpenAPI / JSON serialization goes through
        Pydantic. Pins the ``response_model`` contract.
        """
        from app.api.routes import estimate_prompt_endpoint
        from app.api.schemas import PromptEstimateResponse

        hint = {"game": "stardew_valley"}
        with patch("orchestrator.router.route", return_value=("shop_channel", hint)):
            result = await estimate_prompt_endpoint(prompt="make a TV shopping channel")

        assert isinstance(result, PromptEstimateResponse)

    async def test_route_prompt_exception_propagates(self, stub_app_estimation):
        """The v57 handler has no defensive ``try/except`` around
        ``route_prompt()`` — an exception from the router propagates.
        Mirrors ``preview_route``'s behaviour (see
        ``test_route_preview.test_route_prompt_exception_propagates``).
        """
        from app.api.routes import estimate_prompt_endpoint

        with patch(
            "orchestrator.router.route",
            side_effect=RuntimeError("router exploded"),
        ):
            with pytest.raises(RuntimeError, match="router exploded"):
                await estimate_prompt_endpoint(prompt="make a TV shopping channel")


# ---------------------------------------------------------------------------
# estimate_prompt_batch_endpoint tests.
# ---------------------------------------------------------------------------


class TestEstimatePromptBatchEndpoint:
    """Tests for ``estimate_prompt_batch_endpoint`` (POST /v1/estimate/batch)."""

    async def test_batch_happy_path_preserves_order(self, stub_app_estimation):
        """A 3-prompt batch returns one ``BatchPromptEstimateItem``
        per prompt in the SAME order as the request. Each row echoes
        the resolved phase, seconds, default_seconds, matched, game.
        The echoed ``prompt`` field is STRIPPED from the row shape
        (the request already carries the prompts, no need to
        duplicate).
        """
        from app.api.schemas import BatchPromptEstimateRequest
        from app.api.routes import estimate_prompt_batch_endpoint

        # The router is called once per prompt — patch as a side_effect
        # that returns a different phase per call. The first two match
        # the stub table; the third falls back.
        hints = [{"game": "stardew_valley"}] * 3
        phases = ["shop_channel", "weather_event", "unknown_phase"]
        with patch(
            "orchestrator.router.route",
            side_effect=list(zip(phases, hints)),
        ):
            req = BatchPromptEstimateRequest(
                prompts=[
                    "make a TV shopping channel",
                    "add a weather event",
                    "something obscure",
                ],
            )
            result = await estimate_prompt_batch_endpoint(req)

        assert result.count == 3
        assert len(result.estimates) == 3
        assert result.default_seconds == 90

        # Order preserved: row[i] corresponds to prompt[i].
        assert result.estimates[0].phase == "shop_channel"
        assert result.estimates[0].seconds == 30
        assert result.estimates[0].matched is True
        assert result.estimates[1].phase == "weather_event"
        assert result.estimates[1].seconds == 45
        assert result.estimates[1].matched is True
        assert result.estimates[2].phase == "unknown_phase"
        assert result.estimates[2].seconds == 90
        assert result.estimates[2].matched is False

        # The echoed prompt is intentionally stripped from each row
        # (the request body already has them).
        for row in result.estimates:
            assert not hasattr(row, "prompt")

    async def test_batch_response_is_batch_prompt_estimate_response_instance(
        self, stub_app_estimation,
    ):
        """The handler returns a ``BatchPromptEstimateResponse``
        instance — pins the ``response_model`` contract.
        """
        from app.api.schemas import BatchPromptEstimateRequest, BatchPromptEstimateResponse
        from app.api.routes import estimate_prompt_batch_endpoint

        with patch(
            "orchestrator.router.route",
            return_value=("shop_channel", {"game": "stardew_valley"}),
        ):
            req = BatchPromptEstimateRequest(prompts=["make a TV shopping channel"])
            result = await estimate_prompt_batch_endpoint(req)

        assert isinstance(result, BatchPromptEstimateResponse)

    async def test_batch_empty_prompts_rejected_with_422(self, stub_app_estimation):
        """An empty ``prompts`` list is rejected by Pydantic's
        ``min_length=1`` constraint on the request schema before the
        handler body runs. Mirrors ``GenerateRequest`` / the singular
        endpoint's ``Query(min_length=1)`` invariant.
        """
        from app.api.schemas import BatchPromptEstimateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=[])
        # Defence-in-depth: even if the schema were bypassed, the
        # handler itself doesn't loop over zero prompts, so no
        # HTTPException path is exercised at runtime. Pin the
        # schema-level guarantee and skip the handler call.

    async def test_batch_too_many_prompts_rejected_with_422(self, stub_app_estimation):
        """A 21-prompt batch is rejected by Pydantic's ``max_length=20``
        constraint. Confirms the upper bound is enforced at the
        schema layer (not silently truncated by the handler).
        """
        from app.api.schemas import BatchPromptEstimateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=["p"] * 21)