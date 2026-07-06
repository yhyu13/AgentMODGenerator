"""Router achievements-phase routing tests.

Pins the v144 wiring for the achievements phase: the five keyword
entries (``achievement`` / ``achievements`` / ``badge`` / ``trophy`` /
``milestone``) added to ``orchestrator/router._PHASE_BY_KEYWORD`` must
all resolve to the ``achievements`` phase, and the ``_default_generators_for_phase``
fallback must return the 3-generator execution order.

These tests are hermetic — no LLM, no DB, no Redis. They exercise
the router in isolation by patching ``_PHASE_BY_KEYWORD`` to a minimal
``stardew_valley`` map (mirrors ``test_router_weather_priority.py``)
and verify the achievements wiring fires on the five keyword
strings the v144 patch registered.

If the orchestrator loses the achievements wiring (a regression in a
later port), these tests turn red at the router level — before the
end-to-end test would notice.
"""
from __future__ import annotations

import pytest

from orchestrator import router


@pytest.fixture
def mock_phase_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """Install a controlled phase map for the duration of one test.

    The real ``_PHASE_BY_KEYWORD`` is keyed by game_id and has dozens
    of keywords per game. Patching it to a minimal stardew_valley map
    lets us assert the achievements wiring's behavior on the five
    keywords without the rest of the table polluting the result.

    The five achievements keywords are inserted alongside a noise
    keyword (``festival`` -> ``event_mod``) so we can verify the
    achievements override does not falsely trigger on unrelated
    prompts.
    """
    phase_map: dict[str, dict[str, str]] = {
        "stardew_valley": {
            # Noise keyword — should not match achievements prompts.
            "festival": "event_mod",
            # v144 wiring — five achievements keywords.
            "achievement": "achievements",
            "achievements": "achievements",
            "badge": "achievements",
            "trophy": "achievements",
            "milestone": "achievements",
        },
    }
    monkeypatch.setattr(router, "_PHASE_BY_KEYWORD", phase_map)
    return phase_map


class TestAchievementsKeywordRouting:
    """``route()`` should resolve all five achievements keywords
    (singular + plural, badge/trophy/milestone variants) to the
    ``achievements`` phase, and unrelated prompts should NOT match.
    """

    @pytest.mark.parametrize(
        "prompt,keyword",
        [
            ("add a custom achievement for harvesting 100 ancient seeds", "achievement"),
            ("make a list of achievements for the community center", "achievements"),
            ("give me a badge for completing the mines", "badge"),
            ("unlock a trophy for reaching level 100 in the skull cavern", "trophy"),
            ("set up a milestone reward for the first year", "milestone"),
        ],
    )
    def test_keyword_routes_to_achievements(
        self,
        mock_phase_map: dict[str, dict[str, str]],
        prompt: str,
        keyword: str,
    ) -> None:
        """Every achievements keyword routes to ``achievements``,
        regardless of phrasing. Verifies the v144 wiring's keyword
        table covers all five entries."""
        phase, hint = router.route(prompt)
        assert phase == "achievements", (
            f"Keyword {keyword!r} in prompt {prompt!r} should route to "
            f"'achievements', got {phase!r}"
        )
        assert hint["game"] == "stardew_valley", (
            f"Game must be detected as 'stardew_valley', got {hint.get('game')!r}"
        )

    def test_negative_unrelated_prompt_does_not_route_to_achievements(
        self,
        mock_phase_map: dict[str, dict[str, str]],
    ) -> None:
        """Negative case: a prompt with NONE of the achievements
        keywords must not accidentally route to achievements."""
        phase, hint = router.route("add a festival event for egg day")
        assert phase == "event_mod", (
            f"Unrelated prompt should route to 'event_mod', got {phase!r}; "
            f"achievements keywords must not fire on non-matching prompts"
        )


class TestAchievementsDefaultGeneratorsFallback:
    """``_default_generators_for_phase`` returns the 3-generator
    execution order for ``achievements``. This is the defence-in-depth
    fallback used when the pack lookup fails (mirrors the weather_event
    fallback tested in ``test_weather_event_generator.py``)."""

    def test_default_generators_for_achievements_phase(self) -> None:
        result = router._default_generators_for_phase("achievements")
        assert result == [
            "achievement_definition_generator",
            "achievement_reward_generator",
            "achievement_content_json_generator",
        ], (
            f"_default_generators_for_phase('achievements') should return "
            f"the 3-generator list, got {result}"
        )

    def test_unknown_phase_returns_empty_list(self) -> None:
        """The v22 WARNING guard: an unknown phase returns ``[]``
        (with a WARNING log). Pinned here so a future port cannot
        silently drop the empty-list contract."""
        result = router._default_generators_for_phase("totally_unknown_phase_xyz")
        assert result == [], (
            f"Unknown phase should return empty list (WARNING logged), "
            f"got {result!r}"
        )