"""Stardew Valley shop channel feature generators."""
from pydantic import BaseModel

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import (
    generate_structured,
    llm_system_prompt,
)


class ManifestOutput(BaseModel):
    unique_id: str
    name: str
    description: str
    author: str
    version: str
    config_schema: dict[str, dict]


class ShopItemPoolOutput(BaseModel):
    items: list[dict[str, str | int]]


class TVChannelOutput(BaseModel):
    channels: list[dict[str, str | int]]


class MailOutput(BaseModel):
    mail_key: str
    text: str


class ConfigSchemaOutput(BaseModel):
    enabled: bool = True
    shop_day: int = 0
    shop_start_hour: int = 6
    shop_end_hour: int = 22
    min_items: int = 3
    max_items: int = 8
    discount_rate: float = 1.0
    price_variance: float = 0.2


class CatalogPreviewOutput(BaseModel):
    items: list[dict[str, str | int | None]]


class TriggerLogicOutput(BaseModel):
    on_shop_open: list[dict[str, str]]
    on_shop_purchase: list[dict[str, str]]


_MANIFEST_FALLBACK = {
    "Format": "1.29.0",
    "UniqueID": "tv_shopping_network",
    "Name": "TV Shopping Network",
    "Author": "AI Generator",
    "Version": "1.0.0",
    "Description": "A TV shopping channel.",
    "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
    "ConfigSchema": {
        "Enabled": {"AllowValues": ["true", "false"], "Default": "true"},
        "DiscountRate": {"AllowValues": ["0.5", "0.75", "1.0"], "Default": "1.0"},
    },
}


def _get_sdv_item_names() -> list[str]:
    try:
        from generators.packs.stardew_valley import StardewValleyPack
        kb = StardewValleyPack.load_knowledge("item_ids")
        objs = kb.get("objects", {})
        return list(objs.values())[:50]
    except Exception:
        return ["Parsnip Seeds", "Melon Seeds", "Pumpkin Seeds", "Crystalarium"]


class ManifestGenerator(BaseGenerator):
    name = "manifest_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Based on this mod request: "{inp["prompt"]}"

Generate a Content Patcher manifest with:
- unique_id: snake_case mod identifier (e.g. tv_shopping_network)
- name: human-readable mod name
- description: 1-2 sentence description
- author: "AI Generator"
- version: "1.0.0"
- config_schema: with Enabled (true/false), DiscountRate (0.5/0.75/1.0), ShopDay (0-6 where 0=Sunday)

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, ManifestOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            m = ManifestOutput(**result)
            slug = m.unique_id.lower().replace(" ", "_").replace("-", "_")
            out.add_file("manifest.json", {
                "Format": "1.29.0",
                "UniqueID": slug,
                "Name": m.name,
                "Author": m.author,
                "Version": m.version,
                "Description": m.description,
                "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
                "ConfigSchema": m.config_schema,
            })
            out.metadata["mod_slug"] = slug
        except Exception:
            out.add_file("manifest.json", _MANIFEST_FALLBACK)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        manifest = output.files.get("manifest.json")
        if not manifest:
            errors.append("manifest_generator: manifest.json missing")
            return errors
        for f in ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]:
            if f not in manifest:
                errors.append(f"manifest_generator: missing '{f}'")
        return errors


class ShopItemPoolGenerator(BaseGenerator):
    name = "shop_item_pool_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        items = _get_sdv_item_names()
        prompt = f'''Create a Stardew Valley shop item pool for: "{inp["prompt"]}"

Use ONLY valid SDV item names from this list (pick 8-12 items):
{", ".join(items[:40])}

For each item provide:
- ItemType: "Object" or "BigCraftable"
- ItemName: exact SDV item name
- Price: gold (seeds 10-100g, crops 50-400g, artisan 100-1200g, big craftables 200-2500g)
- Stock: 1 for rare items, 5-20 for common'''

        try:
            result = await generate_structured(
                prompt, ShopItemPoolOutput,
                system=llm_system_prompt(),
                max_tokens=1536,
            )
            pool = ShopItemPoolOutput(**result)
            lines = ["ItemType\tItemName\tItemName2\tPrice\tStock"]
            for item in pool.items:
                lines.append(f"{item['ItemType']}\t{item['ItemName']}\t\t{item['Price']}\t{item.get('Stock', 1)}")
            out.add_file("Data/Shops.tsv", "\n".join(lines))
        except Exception:
            out.add_file("Data/Shops.tsv", self._fallback_tsv())
        return out

    def _fallback_tsv(self) -> str:
        return (
            "ItemType\tItemName\tItemName2\tPrice\tStock\n"
            "Object\tParsnip Seeds\t\t50\t10\n"
            "Object\tMelon Seeds\t\t250\t5\n"
            "Object\tPumpkin Seeds\t\t300\t5\n"
            "BigCraftable\tCrystalarium\t\t750\t1\n"
            "BigCraftable\tAuto-Grabber\t\t1200\t1\n"
        )

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("Data/Shops.tsv"):
            errors.append("shop_item_pool_generator: Data/Shops.tsv missing")
        return errors


class TVChannelGenerator(BaseGenerator):
    name = "tv_channel_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create 1-2 Stardew Valley TV channels for: "{inp["prompt"]}"

Each channel needs:
- Name: display name
- ChannelID: snake_case identifier
- Description: 1-line
- IconSheet: "Television" or "TV3"
- IconIndex: 0-4'''

        try:
            result = await generate_structured(
                prompt, TVChannelOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            channels = TVChannelOutput(**result).channels
            out.add_file("data/tv_channels.json", {"channels": channels})
        except Exception:
            out.add_file("data/tv_channels.json", {
                "channels": [{"Name": "Shopping Network", "ChannelID": "shopping",
                             "Description": "Daily shopping deals!", "IconSheet": "Television", "IconIndex": 0}]
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class MailSystemGenerator(BaseGenerator):
    name = "mail_system_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create a Stardew Valley mail letter for: "{inp["prompt"]}"

- mail_key: snake_case identifier
- text: use @ for generic, ^ splits paragraphs, - signs off

Keep brief and in-character.'''

        try:
            result = await generate_structured(
                prompt, MailOutput,
                system=llm_system_prompt(),
                max_tokens=512,
            )
            mail = MailOutput(**result)
            out.add_file(f"mail/{mail.mail_key}.json", {mail.mail_key: mail.text})
            out.metadata["mail_key"] = mail.mail_key
        except Exception:
            out.add_file("mail/tv_shopping_broadcast.json", {
                "tv_shopping_broadcast": "Dear @, ^Welcome to the TV Shopping Network!^  - The TV Shopping Network"
            })
            out.metadata["mail_key"] = "tv_shopping_broadcast"
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        if not any(k.startswith("mail/") for k in output.files):
            return ["mail_system_generator: no mail file generated"]
        return []


class ItemSpritesGenerator(BaseGenerator):
    name = "item_sprites_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("assets/sprites/shop_logo.json", {
            "UseExisting": True,
            "SourceRect": {"X": 0, "Y": 0, "Width": 32, "Height": 32},
            "SpriteSheet": "Maps/springobjects",
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class UIAssetsGenerator(BaseGenerator):
    name = "ui_assets_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("assets/ui/catalog_background.json", {
            "UseExisting": True,
            "SpriteSheet": "Maps/MenuTiles",
            "SourceRect": {"X": 16, "Y": 16, "Width": 16, "Height": 16},
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class CatalogPreviewGenerator(BaseGenerator):
    name = "catalog_preview_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create a catalog preview for: "{inp["prompt"]}"

Generate 4-6 items with name, price, and a 1-line SDV-style description.'''

        try:
            result = await generate_structured(
                prompt, CatalogPreviewOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            catalog = CatalogPreviewOutput(**result)
            out.add_file("catalog_preview.json", {
                "shop_name": "TV Shopping Network",
                "broadcast_day": "Sunday",
                "items": catalog.items,
            })
        except Exception:
            out.add_file("catalog_preview.json", {
                "shop_name": "TV Shopping Network",
                "broadcast_day": "Sunday",
                "items": [
                    {"name": "Melon Seeds", "price": 250, "description": "Grows into a juicy melon."},
                ],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class RealismDamageGenerator(BaseGenerator):
    name = "realism_damage_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Generate balance modifiers for: "{inp["prompt"]}"

Include DamageMultiplier (1.0 = normal) and optional PriceScaling. Keep close to 1.0 for balance.'''

        try:
            result = await generate_structured(
                prompt,
                {"type": "object", "properties": {
                    "damage_multiplier": {"type": "number"},
                    "price_scaling": {"type": "object", "properties": {
                        "enabled": {"type": "boolean"},
                        "factor": {"type": "number"},
                    }},
                }, "required": ["damage_multiplier"]},
                system=llm_system_prompt(),
                max_tokens=512,
            )
            out.add_file("data/damage_modifiers.json", {
                "ModID": "TVShoppingNetwork",
                "DamageMultiplier": result.get("damage_multiplier", 1.0),
                "PriceScaling": result.get("price_scaling", {"enabled": False, "factor": 1.0}),
            })
        except Exception:
            out.add_file("data/damage_modifiers.json", {
                "ModID": "TVShoppingNetwork",
                "DamageMultiplier": 1.0,
                "PriceScaling": {"enabled": True, "factor": 1.0},
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class TriggerLogicGenerator(BaseGenerator):
    name = "trigger_logic_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Generate Content Patcher trigger actions for: "{inp["prompt"]}"

Include OnShopOpen (e.g. Mail trigger) and OnShopPurchase (e.g. PlaySound, RemoveItem).
Use valid Content Patcher action names only.'''

        try:
            result = await generate_structured(
                prompt, TriggerLogicOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            triggers = TriggerLogicOutput(**result)
            out.add_file("data/trigger_actions.json", {
                "OnShopOpen": triggers.on_shop_open,
                "OnShopPurchase": triggers.on_shop_purchase,
            })
        except Exception:
            out.add_file("data/trigger_actions.json", {
                "OnShopOpen": [{"Action": "Mail", "Mail": "tv_shopping_broadcast"}],
                "OnShopPurchase": [{"Action": "PlaySound", "Sound": "purchase"}],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("data/trigger_actions.json"):
            errors.append("trigger_logic_generator: trigger_actions.json missing")
        return errors


class ConfigSchemaGenerator(BaseGenerator):
    name = "config_schema_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Generate a config schema for: "{inp["prompt"]}"

Options: Enabled (bool), ShopDay (0-6), ShopStartHour (6-22), ShopEndHour (6-22),
MinItems/MaxItems (item counts), DiscountRate (0.5/0.75/1.0), PriceVariance (0.0-0.5).'''

        try:
            result = await generate_structured(
                prompt, ConfigSchemaOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            cfg = ConfigSchemaOutput(**result)
            out.add_file("config.json", {
                "Enabled": cfg.enabled,
                "ShopDay": cfg.shop_day,
                "ShopStartHour": cfg.shop_start_hour,
                "ShopEndHour": cfg.shop_end_hour,
                "MinItems": cfg.min_items,
                "MaxItems": cfg.max_items,
                "DiscountRate": cfg.discount_rate,
                "PriceVariance": cfg.price_variance,
            })
        except Exception:
            out.add_file("config.json", {
                "Enabled": True, "ShopDay": 0, "ShopStartHour": 6, "ShopEndHour": 22,
                "MinItems": 3, "MaxItems": 8, "DiscountRate": 1.0, "PriceVariance": 0.2,
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("config.json"):
            errors.append("config_schema_generator: config.json missing")
        return errors