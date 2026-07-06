"""Tests for the GET /v1/route_preview endpoint.

Pins the handler-direct behaviour of ``preview_route``
(routes.py:1160) — the dry-run routing decision, the
whitespace-only rejection, the comma-separated ``locales`` split
+ dedup. Schema invariants live in
``test_route_preview_response_schema.py`` (or whatever carries them
on master).

Hermetic: patches ``orchestrator.router.route`` directly (the
handler imports ``route as route_prompt`` at call time inside its
function body, routes.py:1234). Mirrors the recipe in
``test_phase_detail_endpoint.py`` / ``test_list_packs.py`` — no
DB / Redis / LLM I/O.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.api.schemas import RoutePreviewResponse


def _hint(
    *,
    game: str = "stardew_valley",
    confidence: float = 0.5,
    matched_keyword: str = "tv",
    generators: list[str] | None = None,
) -> dict:
    """Build the ``hint`` dict the real router returns alongside the phase.

    The handler reads ``game`` / ``confidence`` / ``matched_keyword`` /
    ``generators`` from the second tuple element (routes.py:1273-1286).
    """
    return {
        "game": game,
        "confidence": confidence,
        "matched_keyword": matched_keyword,
        "generators": list(generators) if generators is not None else [
            "tv_channel_gen",
            "tv_schedule_gen",
        ],
    }


def _router_return(
    *,
    phase: str = "shop_channel",
    hint: dict | None = None,
) -> tuple[str, dict]:
    """Build the ``(phase, hint)`` tuple the real router returns."""
    return (phase, hint if hint is not None else _hint())


class TestRoutePreviewHappyPath:
    """Matched-phase path: all 7 envelope fields populated."""

    async def test_matched_phase_populates_all_seven_fields(self):
        from app.api.routes import preview_route

        with patch(
            "orchestrator.router.route",
            return_value=_router_return(
                phase="shop_channel",
                hint=_hint(
                    game="stardew_valley",
                    confidence=0.75,
                    matched_keyword="shop",
                    generators=["texture_gen", "shop_channel_gen"],
                ),
            ),
        ):
            result = await preview_route(prompt="make a shop channel")

        assert isinstance(result, RoutePreviewResponse)
        assert result.prompt == "make a shop channel"
        assert result.game == "stardew_valley"
        assert result.phase == "shop_channel"
        assert result.generators == ["texture_gen", "shop_channel_gen"]
        assert result.confidence == 0.75
        assert result.matched_keyword == "shop"
        # No ``locales`` query → empty list echoed back.
        assert result.locales == []


class TestRoutePreviewFallback:
    """No keyword matched → ``confidence=0.0``, ``matched_keyword=""``."""

    async def test_unmatched_prompt_yields_zero_confidence(self):
        """The router's default phase has ``matched_keyword=""`` and
        ``confidence=0.0``. The handler must echo those verbatim
        rather than synthesising fake match metadata."""
        from app.api.routes import preview_route

        with patch(
            "orchestrator.router.route",
            return_value=_router_return(
                phase="default_phase",
                hint=_hint(
                    game="stardew_valley",
                    confidence=0.0,
                    matched_keyword="",
                    generators=["fallback_gen"],
                ),
            ),
        ):
            result = await preview_route(prompt="asdfghjkl random text")

        assert result.phase == "default_phase"
        assert result.confidence == 0.0
        assert result.matched_keyword == ""
        assert result.generators == ["fallback_gen"]


class TestRoutePreviewLocalesSplit:
    """Comma-separated ``locales`` is split + deduped."""

    async def test_locales_split_dedup_and_whitespace_tolerance(self):
        """``locales="fr,de,ja"`` echoes ``["fr", "de", "ja"]``,
        ``" fr , de , ja "`` strips per-entry whitespace, and
        ``"fr,de,fr,de"`` dedupes to ``["fr", "de"]``. v38 first-
        cut: no BCP-47 shape validation (that's a v39+ follow-up)."""
        from app.api.routes import preview_route

        with patch(
            "orchestrator.router.route",
            return_value=_router_return(),
        ):
            r1 = await preview_route(prompt="shop channel", locales="fr,de,ja")
            r2 = await preview_route(
                prompt="shop channel", locales=" fr , de , ja "
            )
            r3 = await preview_route(
                prompt="shop channel", locales="fr,de,fr,de"
            )

        assert r1.locales == ["fr", "de", "ja"]
        assert r2.locales == ["fr", "de", "ja"]
        assert r3.locales == ["fr", "de"]


class TestRoutePreviewWhitespaceRejection:
    """Defensive trim rejects whitespace-only prompts with 422."""

    async def test_whitespace_only_prompt_raises_422_and_skips_router(self):
        """``Query(min_length=1)`` only rejects the empty string —
        the handler's defensive trim catches a prompt that's all
        whitespace (``"   "``) and raises a 422 before the router
        is called. The router must NOT be invoked for an invalid
        prompt — load-bearing invariant (a whitespace-only prompt
        would otherwise produce a low-quality routing decision)."""
        from fastapi import HTTPException

        from app.api.routes import preview_route

        with patch("orchestrator.router.route") as mock_route:
            with pytest.raises(HTTPException) as exc_info:
                await preview_route(prompt="   ")

        assert exc_info.value.status_code == 422
        assert "whitespace" in exc_info.value.detail.lower()
        mock_route.assert_not_called()


class TestRoutePreviewEmptyLocales:
    """``locales=None`` / empty string / whitespace → empty list."""

    async def test_none_empty_and_whitespace_locales_yield_empty_list(self):
        """The default (``locales=None``), empty string, and
        whitespace-only ``locales`` all return ``[]`` — the handler
        skips the split entirely. Same convention as the
        ``GenerateRequest`` schema boundary."""
        from app.api.routes import preview_route

        with patch(
            "orchestrator.router.route",
            return_value=_router_return(),
        ):
            r1 = await preview_route(prompt="shop channel")
            r2 = await preview_route(prompt="shop channel", locales="")
            r3 = await preview_route(prompt="shop channel", locales="   ")

        assert r1.locales == []
        assert r2.locales == []
        assert r3.locales == []