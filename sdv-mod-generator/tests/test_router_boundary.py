"""Router boundary tests: cross-game keyword collisions, weather-override
greediness, and the unsupported-request sentinel.

Pins the fixes surfaced by the generator ability-boundary probe:
- ``forge`` (a minecraft keyword) used to hard-fail a Stardew prompt with
  ``Unknown game: minecraft``; it now falls back to stardew_valley.
- ``"a festival where it snows candy"`` was hijacked to ``weather_event``
  by the weather-priority override; explicit event-type words now pin it
  to ``event_mod``.
- quest/fish/monster/machine/skill/crop prompts used to silently become
  ``shop_channel`` mods; they now route to the ``no_support`` sentinel and
  the pipeline fails with a clear ``unsupported_request`` error.
"""
from __future__ import annotations

from orchestrator import router
from orchestrator.pipeline import node_route
from orchestrator.state import PipelineState


class TestCrossGameKeywordCollision:
    def test_forge_prompt_stays_stardew_valley(self) -> None:
        """``forge`` is a minecraft keyword; with no minecraft pack the
        router must fall back to stardew_valley instead of hard-failing."""
        phase, hint = router.route("give the blacksmith Clint a new forge schedule")
        assert hint["game"] == "stardew_valley"
        assert phase == "npc_schedule"

    def test_redstone_prompt_stays_stardew_valley(self) -> None:
        phase, hint = router.route("add a redstone powered door to the farm")
        assert hint["game"] == "stardew_valley"


class TestWeatherOverrideNotGreedy:
    def test_festival_snow_stays_event_mod(self) -> None:
        """An explicit festival prompt with an incidental weather word must
        NOT be hijacked into weather_event."""
        phase, hint = router.route("add a festival where it snows candy in the town square")
        assert phase == "event_mod"
        assert hint["matched_keyword"] == "festival"

    def test_rain_storm_event_still_weather_event(self) -> None:
        """The original override case still works when there is no explicit
        event-type word."""
        phase, _ = router.route("add a rain storm event")
        assert phase == "weather_event"

    def test_celebration_with_storm_stays_event_mod(self) -> None:
        phase, _ = router.route("add a celebration during a summer storm")
        assert phase == "event_mod"


class TestUnsupportedSentinel:
    def _assert_no_support(self, prompt: str, expected_kw: str) -> None:
        phase, hint = router.route(prompt)
        assert phase == "no_support"
        assert hint["generators"] == []
        assert hint["matched_keyword"] == expected_kw
        assert hint["confidence"] == 0.0

    def test_quest_routes_no_support(self) -> None:
        self._assert_no_support(
            "add a quest to find the ancient amulet deep in the mines", "quest"
        )

    def test_machine_routes_no_support(self) -> None:
        self._assert_no_support("add a new machine that turns stone into gold", "machine")

    def test_monster_routes_no_support(self) -> None:
        self._assert_no_support("add a new stone golem monster to the mines", "monster")

    def test_fish_routes_no_support(self) -> None:
        self._assert_no_support("add a custom fish that can be caught in the mines", "fish")

    def test_crop_growth_routes_no_support(self) -> None:
        self._assert_no_support("make crops grow twice as fast during summer", "crops")

    def test_skill_routes_no_support(self) -> None:
        self._assert_no_support("add a fishing mastery skill to the game", "fish")

    def test_default_fallback_still_shop_channel(self) -> None:
        """Prompts with no unsupported keyword still default to shop_channel."""
        phase, hint = router.route("make me a thing")
        assert phase == "shop_channel"
        assert hint["matched_keyword"] == ""


class TestNodeRouteUnsupported:
    def test_no_support_fails_fast_with_clear_error(self) -> None:
        state = PipelineState(
            request_id="req_unsupported",
            user_id="test_user",
            prompt="add a quest to find the ancient amulet",
        )
        result = node_route(state)
        assert result.status == "failed"
        assert result.phase == "no_support"
        assert result.generators == []
        assert any("unsupported_request" in e for e in result.errors)
        assert "quest" in " ".join(result.errors)
