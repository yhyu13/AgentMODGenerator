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
from generators.packs.stardew_valley.features.texture import TextureGenerator

_PKGDIR = Path(__file__).parent
_MANIFEST = GameManifest(
    game_id="stardew_valley",
    display_name="Stardew Valley",
    mod_format="ContentPatcher",
    supported_phases=["shop_channel", "texture"],
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
        raise ValueError(f"Unknown phase for stardew_valley: {phase}")


register_game_pack(StardewValleyPack)