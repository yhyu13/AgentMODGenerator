"""Generator registry."""
from generators.base import BaseGenerator

_GENERATOR_REGISTRY: dict[str, type[BaseGenerator]] = {}


def register(name: str, cls: type[BaseGenerator]) -> None:
    _GENERATOR_REGISTRY[name] = cls


def get(name: str) -> type[BaseGenerator] | None:
    return _GENERATOR_REGISTRY.get(name)


def list_generators() -> list[str]:
    return list(_GENERATOR_REGISTRY.keys())


from generators.p0_texture import TextureGenerator  # noqa: E402
register("texture_generator", TextureGenerator)

from generators.p1_shop_channel import (  # noqa: E402
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
)
register("manifest_generator", ManifestGenerator)
register("shop_item_pool_generator", ShopItemPoolGenerator)
register("tv_channel_generator", TVChannelGenerator)
register("mail_system_generator", MailSystemGenerator)
register("item_sprites_generator", ItemSpritesGenerator)
register("ui_assets_generator", UIAssetsGenerator)
register("catalog_preview_generator", CatalogPreviewGenerator)
register("realism_damage_generator", RealismDamageGenerator)
register("trigger_logic_generator", TriggerLogicGenerator)
register("config_schema_generator", ConfigSchemaGenerator)
