"""Router weather-event priority tests.

Pins the routing decision for prompts that contain both a weather
keyword (rain/storm/snow/wind/weather/buff) and the generic ``event``
word. The main loop in ``route()`` uses longest-keyword-wins with a
strict ``>`` comparison, so when ``"event"`` (5 chars) and ``"storm"``
(5 chars) tie, dict-insertion order picks the first one inserted. The
priority override added in ``orchestrator/router.route()`` flips that
tie so weather-flavoured prompts route to ``weather_event`` instead
of ``event_mod``.

These tests mock ``_PHASE_BY_KEYWORD`` so they do not depend on the
game pack's ``supported_phases`` list. The fix is therefore testable
in isolation today, before the ``weather_event`` phase is added to
the pack. When that phase lands, the override will start firing on
real prompts automatically; no router change required.
"""
from __future__ import annotations

from typing import Any

import pytest

from orchestrator import router


@pytest.fixture
def mock_phase_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """Install a controlled phase map for the duration of one test.

    The real ``_PHASE_BY_KEYWORD`` is keyed by game_id and has dozens
    of keywords per game. Patching it to a minimal stardew_valley map
    lets us assert the override's behavior on the specific tie case
    without the rest of the table polluting the result.
    """
    phase_map: dict[str, dict[str, str]] = {
        "stardew_valley": {
            # Insertion order: ``"event"`` BEFORE ``"storm"``, so
            # longest-keyword-wins (5 == 5) keeps the first match
            # ``event_mod``. The override must then flip it to
            # ``weather_event`` for weather-flavoured prompts.
            "event": "event_mod",
            "storm": "weather_event",
            "rain": "weather_event",
            "festival": "event_mod",
        },
    }
    monkeypatch.setattr(router, "_PHASE_BY_KEYWORD", phase_map)
    return phase_map


class TestWeatherEventPriorityOverride:
    """``route()`` should prefer ``weather_event`` over ``event_mod`` when
    the prompt contains any weather keyword alongside the generic
    ``event`` word."""

    def test_rain_storm_event_routes_to_weather_event(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """The original failing case: ``"add a rain storm event"`` must
        route to ``weather_event``, not ``event_mod``. Without the
        priority override, ``"event"`` (5 chars) and ``"storm"`` (5
        chars) tie; the dict-insertion order keeps ``event_mod``."""
        phase, hint = router.route("add a rain storm event")
        assert phase == "weather_event"
        assert hint["game"] == "stardew_valley"

    def test_storm_alone_routes_to_weather_event(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A bare ``"storm"`` prompt (no ``"event"`` word) routes to
        ``weather_event`` via the main longest-keyword-wins loop on
        the ``storm`` keyword. The override is a no-op here because
        the main loop already picked ``weather_event``."""
        phase, hint = router.route("add a storm")
        assert phase == "weather_event"

    def test_rain_alone_routes_to_weather_event(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """A bare ``"rain"`` prompt routes to ``weather_event`` via the
        main loop on the ``rain`` keyword (4 chars vs no ``event``)."""
        phase, hint = router.route("add a rain")
        assert phase == "weather_event"

    def test_festival_event_stays_event_mod(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """Negative case: a plain festival prompt with NO weather
        keyword still resolves to ``event_mod``. The override must
        not steal non-weather events."""
        phase, hint = router.route("add a festival event")
        assert phase == "event_mod"

    def test_snow_event_routes_to_weather_event(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """``"snow event"`` has a weather keyword (``snow``) and the
        generic ``event`` word; the override fires."""
        phase, hint = router.route("add a snow event")
        assert phase == "weather_event"

    def test_weather_event_routes_to_weather_event(
        self, mock_phase_map: dict[str, dict[str, str]]
    ) -> None:
        """``"weather event"`` has a weather keyword and the generic
        ``event`` word; the override fires (even though ``weather``
        is 7 chars and wins on its own)."""
        phase, hint = router.route("add a weather event")
        assert phase == "weather_event"
