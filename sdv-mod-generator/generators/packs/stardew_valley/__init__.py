"""Stardew Valley generator pack.

This pack contains all generators and knowledge specific to Stardew Valley.
Each game gets its own isolated pack — the pipeline is game-agnostic.
"""
from pathlib import Path

from generators.core import (
    GamePack,
    GameManifest,
    PhaseGenerators,
    register_game_pack,
)
from generators.packs.stardew_valley.features.shop_channel import (
    ManifestGenerator,
    ShopItemPoolGenerator,
    TVChannelGenerator,
    MailSystemGenerator,
    ItemSpritesGenerator,
    UIAssetsGenerator,
    CatalogPreviewGenerator,
    RealismDamageGenerator,
    TriggerLogicGenerator,
    ConfigSchemaGenerator,
    ContentJsonGenerator,
)
from generators.packs.stardew_valley.features.npc_schedule import (
    NPCScheduleGenerator,
    NPCDialogueGenerator,
    NPCGiftTasteGenerator,
    NPCContentJsonGenerator,
)
from generators.packs.stardew_valley.features.texture import TextureGenerator
from generators.packs.stardew_valley.features.event_mod import (
    FestivalScheduleGenerator,
    FestivalShopGenerator,
    FestivalMapGenerator,
    FestivalDialogueGenerator,
    FestivalMailGenerator,
    FestivalContentJsonGenerator,
)
from generators.packs.stardew_valley.features.custom_crafting import (
    CraftingRecipeGenerator,
    CookingRecipeGenerator,
    CraftingContentJsonGenerator,
)
from generators.packs.stardew_valley.features.farm_expansion import (
    BuildingGenerator,
    WarpPointGenerator,
    MapEditGenerator,
    FarmExpansionContentJsonGenerator,
)
from generators.packs.stardew_valley.features.weather_event import (
    WeatherManifestGenerator,
    WeatherEventGenerator,
    WeatherNPCDialogueGenerator,
    WeatherBuffGenerator,
    WeatherMailGenerator,
    WeatherContentJsonGenerator,
)
from generators.packs.stardew_valley.features.achievements import (
    AchievementDefinitionGenerator,
    AchievementRewardGenerator,
    AchievementContentJsonGenerator,
)
from generators.packs.stardew_valley.features.weapon_definition import (
    WeaponDefinitionDefinitionGenerator,
    WeaponDefinitionContentJsonGenerator,
)

_PKGDIR = Path(__file__).parent
_MANIFEST = GameManifest(
    game_id="stardew_valley",
    display_name="Stardew Valley",
    mod_format="ContentPatcher",
    supported_phases=[
        "shop_channel", "texture", "npc_schedule", "event_mod",
        "custom_crafting", "farm_expansion", "weather_event", "achievements",
        "weapon_definition",
    ],
    knowledge_dir=_PKGDIR / "knowledge",
)


class StardewValleyPack(GamePack):
    manifest = _MANIFEST

    @classmethod
    def get_manifest(cls) -> GameManifest:
        return _MANIFEST

    @classmethod
    def list_phases(cls) -> list[str]:
        return _MANIFEST.supported_phases

    @classmethod
    def get_generators(cls, phase: str) -> PhaseGenerators:
        if phase == "shop_channel":
            gens = [
                ManifestGenerator,
                ShopItemPoolGenerator,
                TVChannelGenerator,
                MailSystemGenerator,
                ItemSpritesGenerator,
                UIAssetsGenerator,
                CatalogPreviewGenerator,
                RealismDamageGenerator,
                TriggerLogicGenerator,
                ConfigSchemaGenerator,
                ContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "manifest_generator",
                    "shop_item_pool_generator",
                    "tv_channel_generator",
                    "mail_system_generator",
                    "item_sprites_generator",
                    "ui_assets_generator",
                    "catalog_preview_generator",
                    "realism_damage_generator",
                    "trigger_logic_generator",
                    "config_schema_generator",
                    "content_json_generator",
                ],
            )
        if phase == "texture":
            return PhaseGenerators(
                phase=phase,
                generators=[TextureGenerator],
                execution_order=["texture_generator"],
            )
        if phase == "npc_schedule":
            return PhaseGenerators(
                phase=phase,
                generators=[
                    ManifestGenerator,
                    NPCScheduleGenerator,
                    NPCDialogueGenerator,
                    NPCGiftTasteGenerator,
                    NPCContentJsonGenerator,
                ],
                execution_order=[
                    "manifest_generator",
                    "npc_schedule_generator",
                    "npc_dialogue_generator",
                    "npc_gift_taste_generator",
                    "npc_content_json_generator",
                ],
            )
        if phase == "event_mod":
            gens = [
                FestivalScheduleGenerator,
                FestivalShopGenerator,
                FestivalMapGenerator,
                FestivalDialogueGenerator,
                FestivalMailGenerator,
                FestivalContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "festival_schedule_generator",
                    "festival_shop_generator",
                    "festival_map_generator",
                    "festival_dialogue_generator",
                    "festival_mail_generator",
                    "festival_content_json_generator",
                ],
            )
        if phase == "custom_crafting":
            gens = [
                CraftingRecipeGenerator,
                CookingRecipeGenerator,
                CraftingContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "crafting_recipe_generator",
                    "cooking_recipe_generator",
                    "crafting_content_json_generator",
                ],
            )
        if phase == "farm_expansion":
            gens = [
                ManifestGenerator,
                BuildingGenerator,
                WarpPointGenerator,
                MapEditGenerator,
                FarmExpansionContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "manifest_generator",
                    "building_generator",
                    "warp_point_generator",
                    "map_edit_generator",
                    "farm_expansion_content_json_generator",
                ],
            )
        if phase == "weather_event":
            gens = [
                WeatherManifestGenerator,
                WeatherEventGenerator,
                WeatherNPCDialogueGenerator,
                WeatherBuffGenerator,
                WeatherMailGenerator,
                WeatherContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "weather_manifest_generator",
                    "weather_event_generator",
                    "weather_npc_dialogue_generator",
                    "weather_buff_generator",
                    "weather_mail_generator",
                    "weather_content_json_generator",
                ],
            )
        if phase == "achievements":
            gens = [
                AchievementDefinitionGenerator,
                AchievementRewardGenerator,
                AchievementContentJsonGenerator,
            ]
            return PhaseGenerators(
                phase=phase,
                generators=gens,
                execution_order=[
                    "achievement_definition_generator",
                    "achievement_reward_generator",
                    "achievement_content_json_generator",
                ],
            )
        if phase == "weapon_definition":
            # v170 — both generators registered. DefinitionGenerator
            # runs first and emits assets/weapon_definition/weapons.json;
            # ContentJsonGenerator reads that prior_outputs and emits
            # content.json with the Data/Weapons + Strings/UI changes.
            return PhaseGenerators(
                phase=phase,
                generators=[
                    WeaponDefinitionDefinitionGenerator,
                    WeaponDefinitionContentJsonGenerator,
                ],
                execution_order=[
                    "weapon_definition_definition_generator",
                    "weapon_definition_content_json_generator",
                ],
            )
        raise ValueError(f"Unknown phase for stardew_valley: {phase}")


register_game_pack(StardewValleyPack)