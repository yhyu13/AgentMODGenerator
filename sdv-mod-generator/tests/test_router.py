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
        phase, hint = route("replace the parsnip crop texture")
        assert phase == "texture"
        assert hint["game"] == "stardew_valley"
        # Manifest-first order since the MVP audit (texture was
        # manifestless and produced unloadable zips standalone).
        assert hint["generators"] == ["manifest_generator", "texture_generator"]

    def test_sprite_routing(self):
        phase, hint = route("make a pixel art sprite of a glowing blue carp")
        assert phase == "sprite"
        assert hint["game"] == "stardew_valley"
        assert hint["generators"] == ["sprite_generator"]

    def test_shop_keyword_routing(self):
        phase, hint = route("add a shop to my stardew valley game")
        assert phase == "shop_channel"
        assert hint["game"] == "stardew_valley"

    def test_npc_schedule_routing(self):
        phase, hint = route("modify npc schedule in stardew valley")
        assert phase == "npc_schedule"
        assert hint["game"] == "stardew_valley"
        assert "npc_schedule_generator" in hint["generators"]

    def test_event_mod_routing(self):
        phase, hint = route("create a festival event for stardew valley")
        assert phase == "event_mod"
        assert hint["game"] == "stardew_valley"
        assert "festival_schedule_generator" in hint["generators"]
        assert "festival_content_json_generator" in hint["generators"]

    def test_custom_crafting_routing(self):
        phase, hint = route("add custom crafting recipes to stardew valley")
        assert phase == "custom_crafting"
        assert hint["game"] == "stardew_valley"
        assert "crafting_recipe_generator" in hint["generators"]
        assert "cooking_recipe_generator" in hint["generators"]
        assert "crafting_content_json_generator" in hint["generators"]

    def test_cooking_keyword_routing(self):
        phase, hint = route("new cooking recipes for stardew")
        assert phase == "custom_crafting"
        assert hint["game"] == "stardew_valley"
        assert "cooking_recipe_generator" in hint["generators"]

    def test_execution_order_set(self):
        phase, hint = route("tv shopping channel")
        assert hint["execution_order"] == hint["generators"]

    def test_farm_expansion_routing(self):
        phase, hint = route("create a farm expansion with new buildings")
        assert phase == "farm_expansion"
        assert hint["game"] == "stardew_valley"
        assert "building_generator" in hint["generators"]
        assert "warp_point_generator" in hint["generators"]
        assert "map_edit_generator" in hint["generators"]
        assert "farm_expansion_content_json_generator" in hint["generators"]

    def test_building_keyword_routing(self):
        phase, hint = route("add custom buildings to stardew valley")
        assert phase == "farm_expansion"
        assert "building_generator" in hint["generators"]

    def test_warp_keyword_routing(self):
        phase, hint = route("add warp points to my farm")
        assert phase == "farm_expansion"
        assert "warp_point_generator" in hint["generators"]

    def test_map_edit_keyword_routing(self):
        phase, hint = route("map edit for farm")
        assert phase == "farm_expansion"
        assert "map_edit_generator" in hint["generators"]


class TestNPCScheduleRouting:
    def test_npc_schedule_has_generators(self):
        phase, hint = route("create a new NPC with daily schedule")
        assert phase == "npc_schedule"
        assert "npc_schedule_generator" in hint["generators"]
        assert "npc_dialogue_generator" in hint["generators"]
        assert "npc_gift_taste_generator" in hint["generators"]
        assert "npc_content_json_generator" in hint["generators"]

    def test_npc_schedule_execution_order(self):
        phase, hint = route("npc schedule for stardew valley")
        order = hint["execution_order"]
        assert order.index("manifest_generator") < order.index("npc_schedule_generator")
        assert order.index("npc_schedule_generator") < order.index("npc_dialogue_generator")
        assert order.index("npc_dialogue_generator") < order.index("npc_content_json_generator")


class TestCustomCraftingRouting:
    def test_custom_crafting_execution_order(self):
        phase, hint = route("custom crafting and cooking recipes")
        assert phase == "custom_crafting"
        order = hint["execution_order"]
        assert order.index("crafting_recipe_generator") < order.index("cooking_recipe_generator")
        assert order.index("cooking_recipe_generator") < order.index("crafting_content_json_generator")


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
