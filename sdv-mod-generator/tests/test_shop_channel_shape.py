"""shop_channel content.json shape regression tests.

Pins the claude audit §1.3 fix: ``shop_channel`` emitted ``content.json``
as a bare list while every other phase emits the CP 2.x object root
(``Format`` + ``Changes``). T1 also enforced the wrong list shape — both
are now fixed. A list-root content.json still passes T1 (legacy CP 1.x
tolerance) but the generator itself must emit the object root.
"""
from __future__ import annotations

import asyncio

from generators.core import GeneratorOutput
from generators.packs.stardew_valley.features.shop_channel import ContentJsonGenerator
from quality.gate_t1 import run_t1

VALID_CP_WHEN_TOKENS = frozenset({
    "Day", "DayEvent", "DayOfWeek", "DaysPlayed", "Season", "Year", "Weather",
    "HasActiveQuest", "HasCaughtFish", "HasCookingRecipe", "HasCraftingRecipe",
    "HasConversationTopic", "HasFlag", "HasProfession", "HasReadLetter",
    "HasSeenEvent", "HasVisitedLocation", "DailyLuck", "HasDialogueAnswer",
    "HasWalletItem", "IsMainPlayer", "IsOutdoors", "LocationContext",
    "LocationName", "LocationOwnerId", "LocationUniqueName", "PlayerGender",
    "PlayerName", "PreferredPet", "SkillLevel", "ChildNames", "ChildGenders",
    "Hearts", "Relationship", "Roommate", "Spouse", "FarmCave",
    "FarmhouseUpgrade", "FarmMapAsset", "FarmName", "FarmType",
    "IsCommunityCenterComplete", "IsJojaMartComplete", "HavingChild",
    "Pregnant", "Time", "Count", "Query", "Range", "Round", "Lowercase",
    "Merge", "PathPart", "Random", "Render", "Uppercase", "FirstValidFile",
    "HasMod", "HasFile", "HasValue", "I18n", "Language", "ModId",
    "AbsoluteFilePath", "FormatAssetName", "InternalAssetKey", "FromFile",
    "Target", "TargetWithoutPath", "TargetPathOnly",
})


def _run_content_generator(prior: dict | None = None) -> GeneratorOutput:
    inp = {
        "prompt": "make a tv shopping network channel",
        "hint": {"game": "stardew_valley", "phase": "shop_channel", "generators": []},
        "request_id": "req_shop_shape",
        "game": "stardew_valley",
        "prior_outputs": prior or {},
        "t2_feedback": "",
    }
    gen = ContentJsonGenerator()
    return asyncio.run(gen.generate(inp))


class TestShopChannelShape:
    def test_content_json_is_object_root(self):
        out = _run_content_generator()
        content = out.files.get("content.json")
        assert content is not None
        assert isinstance(content, dict), "content.json must be a CP 2.x object root"
        assert "Format" in content
        assert isinstance(content.get("Changes"), list)
        assert content["Changes"], "Changes must not be empty"
        for action in content["Changes"]:
            assert isinstance(action, dict)
            assert "Action" in action

    def test_validate_output_accepts_object_root(self):
        out = _run_content_generator()
        errors = ContentJsonGenerator().validate_output(out)
        assert errors == [], errors

    def test_validate_output_rejects_bare_list(self):
        out = GeneratorOutput()
        out.add_file("content.json", [{"Action": "Load"}])
        errors = ContentJsonGenerator().validate_output(out)
        assert any("object root" in e for e in errors)

    def test_all_when_tokens_are_valid_cp_tokens(self):
        shop = GeneratorOutput()
        shop.add_file(
            "assets/data/shops.tsv",
            "ItemType\tItemName\tItemName2\tPrice\tStock\nObject\tParsnip Seeds\t\t50\t10\n",
        )
        catalog = GeneratorOutput()
        catalog.add_file("assets/data/catalog_preview.json", {
            "shop_name": "TV Shopping Network",
            "broadcast_day": "Saturday",
            "items": [{"name": "Melon Seeds", "price": 250, "description": "Grows into a juicy melon."}],
        })
        mail = GeneratorOutput()
        mail.add_file("mail/tv_shopping_broadcast.json", {"tv_shopping_broadcast": "Dear @"})
        mail.add_file("mail/tv_shopping_delivery.json", {"tv_shopping_delivery": "Dear @"})
        mail.metadata["mail_keys"] = ["tv_shopping_broadcast", "tv_shopping_delivery"]
        mail.metadata["broadcast_key"] = "tv_shopping_broadcast"
        mail.metadata["purchase_key"] = "tv_shopping_delivery"
        prior = {
            "shop_item_pool_generator": shop,
            "catalog_preview_generator": catalog,
            "mail_system_generator": mail,
        }

        out = _run_content_generator(prior)
        content = out.files["content.json"]
        when_actions = [a for a in content["Changes"] if a.get("When")]
        assert len(when_actions) == 3, "expected tv_channels, Data/Shops, and catalog_preview When actions"
        for action in when_actions:
            for key in action["When"]:
                assert key in VALID_CP_WHEN_TOKENS, f"invalid CP When token {key!r} in {action}"


class TestT1ShapeEnforcement:
    def test_t1_accepts_object_root(self):
        out = GeneratorOutput()
        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": [{"Action": "Load", "Target": "Data/X", "FromFile": "x.json"}],
        })
        result = run_t1("req_t1", {"content_json_generator": out})
        assert result.passed is True, result.errors

    def test_t1_rejects_object_root_missing_changes(self):
        out = GeneratorOutput()
        out.add_file("content.json", {"Format": "1.29.0"})
        result = run_t1("req_t1", {"content_json_generator": out})
        assert result.passed is False
        assert any("Changes" in e for e in result.errors)

    def test_t1_rejects_action_without_action_field(self):
        out = GeneratorOutput()
        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": [{"Target": "Data/X"}],
        })
        result = run_t1("req_t1", {"content_json_generator": out})
        assert result.passed is False
        assert any("missing 'Action'" in e for e in result.errors)

    def test_t1_still_accepts_legacy_list_root(self):
        out = GeneratorOutput()
        out.add_file("content.json", [{"Action": "Load", "Target": "Data/X"}])
        result = run_t1("req_t1", {"content_json_generator": out})
        assert result.passed is True, result.errors

    def test_t1_rejects_non_json_type(self):
        out = GeneratorOutput()
        out.add_file("content.json", 42)
        result = run_t1("req_t1", {"content_json_generator": out})
        assert result.passed is False
        assert any("must be a JSON object or array" in e for e in result.errors)
