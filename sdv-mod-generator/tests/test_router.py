"""Router tests — game detection and phase routing."""

from orchestrator.router import detect_game, route, _PHASE_BY_KEYWORD


class TestDetectGame:
    def test_stardew_valley_keywords(self):
        prompts = [
            ("make a TV shopping channel for stardew valley", "stardew_valley"),
            ("stardew valley npc schedule", "stardew_valley"),
            ("replace texture in stardew", "stardew_valley"),
            ("sdv shop mod", "stardew_valley"),
        ]
        for prompt, expected in prompts:
            assert detect_game(prompt) == expected, f"Failed on: {prompt}"

    def test_stardew_fallback(self):
        assert detect_game("random unrelated prompt") == "stardew_valley"


class TestRoute:
    def test_shop_channel_routing(self):
        phase, hint = route("make a TV shopping channel for stardew valley")
        assert phase == "shop_channel"
        assert hint["game"] == "stardew_valley"
        assert "manifest_generator" in hint["generators"]
        assert "shop_item_pool_generator" in hint["generators"]

    def test_texture_routing(self):
        phase, hint = route("replace the parsnip crop sprite")
        assert phase == "texture"
        assert hint["game"] == "stardew_valley"
        assert hint["generators"] == ["texture_generator"]

    def test_shop_keyword_routing(self):
        phase, hint = route("add a shop to my stardew valley game")
        assert phase == "shop_channel"
        assert hint["game"] == "stardew_valley"

    def test_npc_schedule_routing(self):
        phase, hint = route("modify npc schedule in stardew valley")
        assert phase == "npc_schedule"

    def test_execution_order_set(self):
        phase, hint = route("tv shopping channel")
        assert hint["execution_order"] == hint["generators"]


class TestPhaseByKeyword:
    def test_all_phases_have_keywords(self):
        for game, phase_map in _PHASE_BY_KEYWORD.items():
            assert "shop" in phase_map, f"{game} missing shop keyword"
            assert "texture" in phase_map, f"{game} missing texture keyword"

    def test_shop_channel_keyword_coverage(self):
        sdv = _PHASE_BY_KEYWORD["stardew_valley"]
        assert sdv["tv shopping"] == "shop_channel"
        assert sdv["tv"] == "shop_channel"
        assert sdv["mail"] == "shop_channel"
        assert sdv["letter"] == "shop_channel"
