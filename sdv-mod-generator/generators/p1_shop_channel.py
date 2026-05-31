"""P1 shop channel generators — LLM-powered."""

from pydantic import BaseModel

from generators.base import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import (
    generate_structured,
    llm_system_prompt,
    get_item_ids,
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


class ManifestGenerator(BaseGenerator):
    name = "manifest_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Based on this mod request: "{inp['prompt']}"

Generate a Content Patcher manifest with:
- unique_id: snake_case mod identifier (e.g. tv_shopping_network)
- name: human-readable mod name
- description: 1-2 sentence description
- author: "AI Generator"
- version: "1.0.0"
- config_schema: with Enabled (true/false), DiscountRate (0.5/0.75/1.0), ShopDay (0-6 where 0=Sunday)"""

        try:
            result = await generate_structured(
                prompt,
                ManifestOutput,
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
            out.add_file("manifest.json", self._fallback_manifest())
        return out

    def _fallback_manifest(self) -> dict:
        return {
            "Format": "1.29.0",
            "UniqueID": "tv_shopping_network",
            "Name": "TV Shopping Network",
            "Author": "AI Generator",
            "Version": "1.0.0",
            "Description": "A TV shopping channel that sells items.",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
            "ConfigSchema": {
                "Enabled": {"AllowValues": ["true", "false"], "Default": "true"},
                "DiscountRate": {"AllowValues": ["0.5", "0.75", "1.0"], "Default": "1.0"},
            },
        }

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        manifest = output.files.get("manifest.json")
        if not manifest:
            errors.append("manifest_generator: manifest.json missing")
            return errors
        required = ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]
        for field_name in required:
            if field_name not in manifest:
                errors.append(f"manifest_generator: missing required field '{field_name}'")
        return errors


class ShopItemPoolGenerator(BaseGenerator):
    name = "shop_item_pool_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        items_data = get_item_ids()
        available_objects = items_data.get("objects", {})

        prompt = f"""Create a Stardew Valley shop item pool for this mod: "{inp['prompt']}"

Use ONLY valid SDV item names from this list (pick 8-12 items):
{list(available_objects.values())[:50]}

For each item provide:
- ItemType: "Object" or "BigCraftable" (check big_craftables list)
- ItemName: exact SDV item name
- Price: gold price (use realism: seeds 10-100g, crops 50-400g, artisan goods 100-1200g, big craftables 200-2500g)
- Stock: typically 1 for seeds/rare items, 5-20 for common items

Output a JSON list with these fields."""

        try:
            result = await generate_structured(
                prompt,
                ShopItemPoolOutput,
                system=llm_system_prompt(),
                max_tokens=1536,
            )
            pool = ShopItemPoolOutput(**result)
            tsv_lines = ["ItemType\tItemName\tItemName2\tPrice\tStock"]
            for item in pool.items:
                tsv_lines.append(
                    f"{item['ItemType']}\t{item['ItemName']}\t\t{item['Price']}\t{item.get('Stock', 1)}"
                )
            out.add_file("Data/Shops.tsv", "\n".join(tsv_lines))
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
            "Object\tDeluxe Speed-Gro\t\t40\t20\n"
        )

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        shops_tsv = output.files.get("Data/Shops.tsv", "")
        if not shops_tsv:
            errors.append("shop_item_pool_generator: Data/Shops.tsv missing")
        return errors


class TVChannelGenerator(BaseGenerator):
    name = "tv_channel_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Create a SDV TV channel definition for this mod: "{inp['prompt']}"

Generate 1-2 TV channels with:
- Name: channel display name
- ChannelID: snake_case identifier
- Description: 1-line description
- IconSheet: use "Television" or "TV3" etc.
- IconIndex: 0-4

Respond with JSON having a "channels" list."""

        try:
            result = await generate_structured(
                prompt,
                TVChannelOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            channels = TVChannelOutput(**result).channels
            out.add_file("data/tv_channels.json", {"channels": channels})
        except Exception:
            out.add_file("data/tv_channels.json", {
                "channels": [{
                    "Name": "Shopping Network",
                    "ChannelID": "shopping",
                    "Description": "Daily shopping deals on TV!",
                    "IconSheet": "Television",
                    "IconIndex": 0,
                }]
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class MailSystemGenerator(BaseGenerator):
    name = "mail_system_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Create a Stardew Valley mail/letter for: "{inp['prompt']}"

Mail format:
- mail_key: letter identifier (snake_case)
- text: letter body. Use Stardew mail syntax:
  - Start with character name or @ for generic
  - ^ marks end of first paragraph (before the break)
  - Use - as signature line

Keep it brief, in-character for the mod theme."""

        try:
            result = await generate_structured(
                prompt,
                MailOutput,
                system=llm_system_prompt(),
                max_tokens=512,
            )
            mail = MailOutput(**result)
            out.add_file(
                f"mail/{mail.mail_key}.json",
                {mail.mail_key: mail.text},
            )
            out.metadata["mail_key"] = mail.mail_key
        except Exception:
            out.add_file("mail/tv_shopping_broadcast.json", {
                "tv_shopping_broadcast": "Dear @, ^Welcome to the TV Shopping Network! Check our Sunday broadcast for special deals.^  - The TV Shopping Network"
            })
            out.metadata["mail_key"] = "tv_shopping_broadcast"
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not any(k.startswith("mail/") for k in output.files):
            errors.append("mail_system_generator: no mail file generated")
        return errors


class ItemSpritesGenerator(BaseGenerator):
    name = "item_sprites_generator"
    phase = "p1_shop_channel"

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
    phase = "p1_shop_channel"

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
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Create a catalog preview (item showcase) for: "{inp['prompt']}"

Generate 4-6 items with:
- name: item name
- price: gold price
- description: 1-line flavor text

Use realistic SDV-style item descriptions."""

        try:
            result = await generate_structured(
                prompt,
                CatalogPreviewOutput,
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
                    {"name": "Crystalarium", "price": 750, "description": "Generates gems over time."},
                ],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class RealismDamageGenerator(BaseGenerator):
    name = "realism_damage_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Generate balance modifiers for: "{inp['prompt']}"

Include:
- DamageMultiplier: 1.0 = normal damage
- PriceScaling: optional price adjustments (factor around 1.0-1.5)

Keep multipliers close to 1.0 for balance."""

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
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Generate Content Patcher trigger actions for: "{inp['prompt']}"

Include:
- OnShopOpen: actions when shop is accessed (e.g. Mail trigger)
- OnShopPurchase: actions when item is bought (e.g. RemoveItem, PlaySound)

Use valid Content Patcher action names only:
- Mail, RemoveItem, PlaySound, AddMail, ShowFrame, etc.

Output JSON with on_shop_open and on_shop_purchase lists."""

        try:
            result = await generate_structured(
                prompt,
                TriggerLogicOutput,
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
                "OnShopPurchase": [
                    {"Action": "PlaySound", "Sound": "purchase"},
                ],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        triggers = output.files.get("data/trigger_actions.json", {})
        if not triggers:
            errors.append("trigger_logic_generator: trigger_actions.json missing")
        return errors


class ConfigSchemaGenerator(BaseGenerator):
    name = "config_schema_generator"
    phase = "p1_shop_channel"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f"""Generate a config schema for: "{inp['prompt']}"

Include options for:
- Enabled: true/false toggle
- ShopDay: 0-6 (0=Sunday)
- ShopStartHour: 6-22
- ShopEndHour: 6-22
- MinItems / MaxItems: item count range
- DiscountRate: 0.5, 0.75, or 1.0
- PriceVariance: 0.0-0.5

Output JSON with all fields and appropriate values."""

        try:
            result = await generate_structured(
                prompt,
                ConfigSchemaOutput,
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
                "Enabled": True,
                "ShopDay": 0,
                "ShopStartHour": 6,
                "ShopEndHour": 22,
                "MinItems": 3,
                "MaxItems": 8,
                "DiscountRate": 1.0,
                "PriceVariance": 0.2,
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        config = output.files.get("config.json")
        if not config:
            errors.append("config_schema_generator: config.json missing")
        return errors