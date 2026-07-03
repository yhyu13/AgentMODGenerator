"""Router routing-confidence and matched-keyword tests.

Pins the v27 Blue addition to ``orchestrator/router.py``: every
``route()`` call now returns a ``RoutingHint`` with two new fields
(``confidence`` and ``matched_keyword``) and the
``router.routed`` info log event carries the same two fields.
The ``router.pack_fallback`` warning log also surfaces
``error_type`` (exception class name) for log-aggregator
grouping. None of the additions change existing behavior — they
are purely additive.

Source: ``docs/_source_router.py.txt`` lines 18-27 (TypedDict),
lines 1502-1591 (``route()`` body), and line 1571
(``error_type`` on the fallback warning).

Conventions (mirrors ``test_router_weather_priority.py``):
- Mock ``_PHASE_BY_KEYWORD`` via ``monkeypatch`` so each test
  exercises the longest-keyword-wins logic and the weather-event
  priority override in isolation, independent of the
  ``stardew_valley`` pack's evolving ``supported_phases`` list.
- Assert on the new TypedDict fields directly, not on log
  captures (the master log surface is intentionally not asserted
  here — that's a separate concern owned by
  ``test_router_logging.py`` once it lands).
"""
from __future__ import annotations

from typing import Any

import pytest

from orchestrator import router


@pytest.fixture
def mock_phase_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """Install a controlled phase map for the duration of one test.

    Same fixture pattern as ``test_router_weather_priority.py``:
    a minimal ``stardew_valley`` map with hand-picked keyword
    lengths so the confidence heuristic is testable without
    cross-test drift.
    """
    phase_map: dict[str, dict[str, str]] = {
        "stardew_valley": {
            # Length 1 chars: short single-word matches (low confidence)
            "shop": "shop_channel",
            "tv": "shop_channel",  # 2 chars
            "npc": "npc_schedule",  # 3 chars
            "rain": "weather_event",  # 4 chars
            # Insertion order: ``"event"`` BEFORE ``"storm"``, so
            # longest-keyword-wins (5 == 5) keeps the first match
            # ``event_mod``. The weather-event override (see
            # ``test_router_weather_priority.py`` for the same
            # setup) then flips the phase to ``weather_event`` and
            # we assert that ``matched_keyword`` is preserved as
            # ``"event"`` through the override.
            "event": "event_mod",
            "storm": "weather_event",  # 5 chars
            # Length ~16 chars: matches that hit the confidence ceiling (1.0)
            "seasonal festival": "event_mod",  # 17 chars -> ceil
            "map edit": "farm_expansion",  # 9 chars
        },
    }
    monkeypatch.setattr(router, "_PHASE_BY_KEYWORD", phase_map)
    return phase_map


class TestMatchedKeyword:
    """``route()`` should populate ``matched_keyword`` with the
    longest-keyword-wins selection so the v27 diagnose surface can
    render the trigger that drove the route decision."""

    def test_matched_keyword_returned_on_long_match(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A 9-char keyword (``map edit``) wins over shorter ones
        and is reported back in the hint's ``matched_keyword``."""
        _, hint = router.route("please do a map edit on my farm")
        assert hint["matched_keyword"] == "map edit"

    def test_matched_keyword_returned_on_short_match(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A 2-char keyword (``tv``) is reported even when shorter
        than the 9-char ``map edit`` baseline — confirms the field
        tracks the actual winner, not the longest table entry."""
        _, hint = router.route("add a tv channel")
        assert hint["matched_keyword"] == "tv"

    def test_matched_keyword_empty_on_fallback(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A prompt with no keyword match falls back to
        ``shop_channel``; the ``matched_keyword`` is the empty
        string so the diagnose surface can render "default
        fallback" rather than the synthetic ``"shop"`` keyword."""
        _, hint = router.route("make me a thing")
        assert hint["matched_keyword"] == ""

    def test_matched_keyword_survives_weather_override(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """The weather-event priority override changes the phase
        but must NOT change ``matched_keyword`` — the field stays
        as the longest original match (``"event"``, 5 chars) so
        the diagnose surface can render "the route was overridden
        after a 5-char 'event' match" rather than the synthetic
        ``"weather_event"`` phase name."""
        phase, hint = router.route("add a rain storm event")
        assert phase == "weather_event"
        assert hint["matched_keyword"] == "event"


class TestConfidence:
    """``route()`` should compute ``confidence`` from the matched
    keyword's character length, capped at 1.0 for the longest
    real keywords (~16 chars). Fallback (no keyword matched) is
    always 0.0."""

    def test_confidence_ceiling_at_longest_keyword(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """``"seasonal festival"`` (17 chars) is at the ceiling;
        confidence is 1.0 (the heuristic caps at 1.0, not scales
        linearly past 16 chars)."""
        _, hint = router.route("add a seasonal festival")
        assert hint["confidence"] == 1.0

    def test_confidence_proportional_for_short_match(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """``"npc"`` (3 chars) scores 0.19 (3 / 16 rounded to 2dp).
        The 0.19 number is the deterministic output of the
        ``min(1.0, round(3 / 16, 2))`` expression — confirms
        the formula is byte-stable across Python versions."""
        phase, hint = router.route("add an npc schedule")
        assert phase == "npc_schedule"
        # Phase is npc_schedule, longest keyword is "npc" (3 chars)
        assert hint["matched_keyword"] == "npc"
        assert hint["confidence"] == round(3 / 16, 2)
        assert hint["confidence"] < 0.25  # low-confidence bucket

    def test_confidence_zero_on_fallback(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A prompt that matches no keyword falls back to
        ``shop_channel`` with confidence exactly 0.0 — not
        ``0.01``, not ``0.001``, the literal zero so the
        orchestrator can branch on ``confidence == 0.0`` as
        the unambiguous fallback signal."""
        phase, hint = router.route("make me a thing")
        assert phase == "shop_channel"
        assert hint["matched_keyword"] == ""
        assert hint["confidence"] == 0.0

    def test_confidence_proportional_for_four_char_keyword(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """Edge case: a 4-char keyword ``"shop"`` scores
        ``round(4 / 16, 2) = 0.25`` (not zero) — confirms that
        the zero-confidence branch is the FALLBACK path, not the
        short-keyword path. A real (short) keyword always returns
        a positive (low) confidence. The 0.25 number is the
        deterministic output of the formula; pinning it here
        guards against accidental formula edits."""
        phase, hint = router.route("add a shop to my stardew")
        assert phase == "shop_channel"
        assert hint["matched_keyword"] == "shop"
        assert hint["confidence"] == round(4 / 16, 2)
        assert hint["confidence"] > 0.0


class TestRoutingHintShape:
    """The v27 TypedDict extension must be backwards compatible
    with the v0-5 fields. These tests pin the full field set on
    a real route call so a future TypedDict edit cannot
    accidentally drop one of the 7 fields."""

    def test_hint_has_all_seven_fields(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """Pin the canonical 7-field shape of ``RoutingHint``.

        v0-5 (master) had 5 fields: game, phase, generators,
        execution_order, dependencies.

        v27 (this round) added 2: confidence, matched_keyword.

        The TypedDict is ``total=True`` (default) so the
        construction site in ``route()`` must supply all 7. A
        future refactor that drops one of the 7 here will break
        this assertion.
        """
        _, hint = router.route("add a shop to my stardew")
        expected_fields = {
            "game",
            "phase",
            "generators",
            "execution_order",
            "dependencies",
            "confidence",
            "matched_keyword",
        }
        assert set(hint.keys()) == expected_fields

    def test_hint_typed_dict_annotations_match(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """The new TypedDict annotations must be ``float`` and
        ``str`` (not ``int`` / ``Optional``). This pins the
        shapes that downstream consumers (e.g. a future Pydantic
        schema for ``GET /v1/router/diagnose``) can rely on
        without runtime isinstance checks."""
        annotations = router.RoutingHint.__annotations__
        assert annotations["confidence"] is float
        assert annotations["matched_keyword"] is str
