"""Stardew Valley shop channel feature generators."""
import zlib

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import (
    generate_structured,
    llm_system_prompt,
)

logger = structlog.get_logger()


def _make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Generate a minimal valid PNG (32-bit RGBA solid color) using stdlib only."""
    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return len(data).to_bytes(4, "big") + chunk_type + data + crc.to_bytes(4, "big")

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    ihdr = png_chunk(b"IHDR", ihdr_data)

    raw_row = bytes([0] + [r, g, b, 255] * width)
    raw_data = b"".join(raw_row for _ in range(height))
    compressed = zlib.compress(raw_data, 9)
    idat = png_chunk(b"IDAT", compressed)
    iend = png_chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


class ManifestOutput(BaseModel):
    unique_id: str = Field(validation_alias="UniqueID")
    name: str = Field(validation_alias="Name")
    description: str = Field(validation_alias="Description")
    author: str = Field(validation_alias="Author")
    version: str = Field(validation_alias="Version")
    config_schema: dict[str, dict] = Field(validation_alias="ConfigSchema")


class ShopItemPoolOutput(BaseModel):
    items: list[dict[str, str | int]] = Field(validation_alias="Items")


class TVChannelOutput(BaseModel):
    channels: list[dict[str, str | int]] = Field(validation_alias="Channels")

    @field_validator("channels", mode="before")
    @classmethod
    def unwrap_channels(cls, v):
        if isinstance(v, dict) and "Channels" in v:
            return v["Channels"]
        return v


class MailOutput(BaseModel):
    mail_key: str
    text: str


class MailListOutput(BaseModel):
    mails: list[MailOutput]


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
    items: list[dict[str, str | int | None]] = Field(validation_alias="Items")


class TriggerLogicOutput(BaseModel):
    on_shop_open: list[dict[str, str | list]] = Field(validation_alias="OnShopOpen")
    on_shop_purchase: list[dict[str, str | list]] = Field(validation_alias="OnShopPurchase")


class RealismDamageOutput(BaseModel):
    damage_multiplier: float = Field(default=1.0, validation_alias="DamageMultiplier")
    price_scaling: dict | None = Field(default=None, validation_alias="PriceScaling")

    @field_validator("damage_multiplier", mode="before")
    @classmethod
    def validate_damage_multiplier(cls, v) -> float:
        if isinstance(v, str):
            try:
                v = float(v)
            except (ValueError, TypeError):
                return 1.0
        if isinstance(v, (int, float)):
            return max(0.1, min(5.0, float(v)))
        return 1.0


def _sanitize_key(key: str) -> str:
    return "".join(c for c in key if c.isalnum() or c in "_-") or "mail"


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
    except (ImportError, AttributeError, KeyError) as exc:
        logger.warning("get_sdv_item_names.fallback", error=str(exc))
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
                max_tokens=4096,
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
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("manifest_generator.failed", error=str(exc))
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
        item_names = _get_sdv_item_names()
        valid_items = set(item_names)
        prompt = f'''Create a Stardew Valley shop item pool for: "{inp["prompt"]}"

Use ONLY valid SDV item names from this list (pick 8-12 items):
{", ".join(item_names[:40])}

For each item provide:
- ItemType: "Object" or "BigCraftable"
- ItemName: exact SDV item name
- Price: gold (seeds 10-100g, crops 50-400g, artisan 100-1200g, big craftables 200-2500g)
- Stock: 1 for rare items, 5-20 for common'''

        try:
            result = await generate_structured(
                prompt, ShopItemPoolOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            pool = ShopItemPoolOutput(**result)
            valid_item_names: list[str] = []
            invalid_items: list[str] = []
            for item in pool.items:
                if item["ItemName"] in valid_items:
                    valid_item_names.append(item["ItemName"])
                else:
                    invalid_items.append(item["ItemName"])
            if invalid_items:
                logger.warning("shop_item_pool.invalid_items", invalid=invalid_items)
            if not valid_item_names:
                logger.warning("shop_item_pool.no_valid_items")
                out.add_file("assets/data/shops.tsv", self._fallback_tsv())
                return out
            lines = ["ItemType\tItemName\tItemName2\tPrice\tStock"]
            for item in pool.items:
                if item["ItemName"] in valid_item_names:
                    lines.append(f"{item['ItemType']}\t{item['ItemName']}\t\t{item['Price']}\t{item.get('Stock', 1)}")
            out.add_file("assets/data/shops.tsv", "\n".join(lines))
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("shop_item_pool_generator.failed", error=str(exc))
            out.add_file("assets/data/shops.tsv", self._fallback_tsv())
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
        if not output.files.get("assets/data/shops.tsv"):
            errors.append("shop_item_pool_generator: assets/data/shops.tsv missing")
        return errors


class TVChannelGenerator(BaseGenerator):
    name = "tv_channel_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create a Stardew Valley TV shopping channel for: "{inp["prompt"]}"

The channel should be called "TV Shopping Network" or similar.
Include:
- Name: display name (e.g. "TV Shopping Network")
- ChannelID: snake_case identifier (e.g. "tv_shopping_network")
- Description: 1-2 lines describing the channel
- IconSheet: "Television" or "TV3"
- IconIndex: 0-4
- BroadcastDay: 6 (Saturday only)'''

        try:
            result = await generate_structured(
                prompt, TVChannelOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            channels = TVChannelOutput(**result).channels
            channel = channels[0] if channels else {
                "Name": "TV Shopping Network",
                "ChannelID": "tv_shopping_network",
                "Description": "Weekly special deals on exclusive items!",
                "IconSheet": "Television",
                "IconIndex": 0,
            }
            channel_id = channel.get("ChannelID", "tv_shopping_network")
            out.add_file("assets/data/tv_channels.json", {"channels": [channel]})
            out.metadata["channel_id"] = channel_id
            out.metadata["channel_name"] = channel.get("Name", "TV Shopping Network")
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("tv_channel_generator.failed", error=str(exc))
            out.add_file("assets/data/tv_channels.json", {
                "channels": [{
                    "Name": "TV Shopping Network",
                    "ChannelID": "tv_shopping_network",
                    "Description": "Weekly special deals on exclusive items!",
                    "IconSheet": "Television",
                    "IconIndex": 0,
                }]
            })
            out.metadata["channel_id"] = "tv_shopping_network"
            out.metadata["channel_name"] = "TV Shopping Network"
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        channels_data = output.files.get("assets/data/tv_channels.json")
        if not channels_data:
            errors.append("tv_channel_generator: assets/data/tv_channels.json missing")
        elif "channels" not in channels_data:
            errors.append("tv_channel_generator: channels key missing")
        return errors


class MailSystemGenerator(BaseGenerator):
    name = "mail_system_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create Stardew Valley mail letters for: "{inp["prompt"]}"

Create 2-3 mail letters:
1. A broadcast announcement mail (contains "broadcast" in the key) that welcomes players
2. A purchase confirmation mail that delivers the purchased item
3. A catalogue preview mail sent after first purchase

For each:
- mail_key: snake_case identifier
- text: use @ for player name, ^ splits paragraphs, - signs off
- Use "broadcast" prefix for TV-triggered mail (these auto-show when player watches TV)

Keep brief and in-character.'''

        try:
            result = await generate_structured(
                prompt, MailListOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            mails = MailListOutput(**result).mails
            mail_keys = []
            for mail in mails:
                safe_key = _sanitize_key(mail.mail_key)
                out.add_file(f"mail/{safe_key}.json", {safe_key: mail.text})
                mail_keys.append(safe_key)
            out.metadata["mail_keys"] = mail_keys
            out.metadata["broadcast_key"] = next((k for k in mail_keys if "broadcast" in k), mail_keys[0] if mail_keys else "tv_shopping_broadcast")
            out.metadata["purchase_key"] = next((k for k in mail_keys if "purchase" in k or "delivery" in k), mail_keys[1] if len(mail_keys) > 1 else mail_keys[0])
            out.metadata["catalogue_key"] = next((k for k in mail_keys if "catalogue" in k or "preview" in k), mail_keys[2] if len(mail_keys) > 2 else None)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("mail_system_generator.failed", error=str(exc))
            out.add_file("mail/tv_shopping_broadcast.json", {
                "tv_shopping_broadcast": "Dear @, ^Welcome to the TV Shopping Network! Your exclusive shopping channel is now available. ^Watch it on TV to see this week's special offers!^  - The TV Shopping Network"
            })
            out.add_file("mail/tv_shopping_delivery.json", {
                "tv_shopping_delivery": "Dear @, ^Your order has arrived! ^You purchased %item.name% from the TV Shopping Network. ^Thank you for shopping with us!^  - The TV Shopping Network"
            })
            out.metadata["mail_keys"] = ["tv_shopping_broadcast", "tv_shopping_delivery"]
            out.metadata["broadcast_key"] = "tv_shopping_broadcast"
            out.metadata["purchase_key"] = "tv_shopping_delivery"
            out.metadata["catalogue_key"] = None
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
        # Generate a 32x32 shop logo sprite (purple/blue TV shopping theme)
        sprite_png = _make_png(32, 32, 128, 80, 200)
        out.add_file("assets/sprites/shop_sprites.png", sprite_png)
        out.add_file("assets/sprites/shop_logo.json", {
            "UseExisting": False,
            "SourceRect": {"X": 0, "Y": 0, "Width": 32, "Height": 32},
            "SpriteSheet": "assets/sprites/shop_sprites.png",
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        sprite_file = output.files.get("assets/sprites/shop_logo.json")
        if not sprite_file:
            errors.append("item_sprites_generator: assets/sprites/shop_logo.json missing")
        return errors


class UIAssetsGenerator(BaseGenerator):
    name = "ui_assets_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        # Generate a 16x16 catalog background tile (warm gold color)
        bg_png = _make_png(16, 16, 200, 160, 80)
        out.add_file("assets/ui/catalog_background.png", bg_png)
        out.add_file("assets/ui/catalog_background.json", {
            "UseExisting": False,
            "SpriteSheet": "assets/ui/catalog_background.png",
            "SourceRect": {"X": 0, "Y": 0, "Width": 16, "Height": 16},
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        ui_file = output.files.get("assets/ui/catalog_background.json")
        if not ui_file:
            errors.append("ui_assets_generator: assets/ui/catalog_background.json missing")
        return errors


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
                max_tokens=4096,
            )
            catalog = CatalogPreviewOutput(**result)
            out.add_file("assets/data/catalog_preview.json", {
                "shop_name": "TV Shopping Network",
                "broadcast_day": "Saturday",
                "items": catalog.items,
            })
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("catalog_preview_generator.failed", error=str(exc))
            out.add_file("assets/data/catalog_preview.json", {
                "shop_name": "TV Shopping Network",
                "broadcast_day": "Saturday",
                "items": [
                    {"name": "Melon Seeds", "price": 250, "description": "Grows into a juicy melon."},
                ],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        preview = output.files.get("assets/data/catalog_preview.json")
        if not preview:
            errors.append("catalog_preview_generator: assets/data/catalog_preview.json missing")
        elif "items" not in preview:
            errors.append("catalog_preview_generator: items key missing")
        return errors


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
                RealismDamageOutput,
                system=llm_system_prompt(),
                max_tokens=1024,
            )
            dmg = RealismDamageOutput(**result)
            out.add_file("assets/data/damage_modifiers.json", {
                "ModID": "TVShoppingNetwork",
                "DamageMultiplier": dmg.damage_multiplier,
                "PriceScaling": dmg.price_scaling or {"enabled": False, "factor": 1.0},
            })
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("realism_damage_generator.failed", error=str(exc))
            out.add_file("assets/data/damage_modifiers.json", {
                "ModID": "TVShoppingNetwork",
                "DamageMultiplier": 1.0,
                "PriceScaling": {"enabled": True, "factor": 1.0},
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        dmg_file = output.files.get("assets/data/damage_modifiers.json")
        if not dmg_file:
            errors.append("realism_damage_generator: assets/data/damage_modifiers.json missing")
        elif "DamageMultiplier" not in dmg_file:
            errors.append("realism_damage_generator: DamageMultiplier missing")
        return errors


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
                max_tokens=4096,
            )
            triggers = TriggerLogicOutput(**result)
            out.add_file("data/trigger_actions.json", {
                "OnShopOpen": triggers.on_shop_open,
                "OnShopPurchase": triggers.on_shop_purchase,
            })
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("trigger_logic_generator.failed", error=str(exc))
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
                max_tokens=4096,
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
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("config_schema_generator.failed", error=str(exc))
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


class ContentJsonGenerator(BaseGenerator):
    name = "content_json_generator"
    phase = "shop_channel"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        mod_id = manifest_data.get("UniqueID", "TVShoppingNetwork").lower()
        config_data = prior.get("config_schema_generator", GeneratorOutput()).files.get("config.json", {})
        tv_data = prior.get("tv_channel_generator", GeneratorOutput()).files.get("assets/data/tv_channels.json", {})
        mail_outputs = prior.get("mail_system_generator", GeneratorOutput())
        shop_tsv = prior.get("shop_item_pool_generator", GeneratorOutput()).files.get("assets/data/shops.tsv", "")
        catalog_data = prior.get("catalog_preview_generator", GeneratorOutput()).files.get("assets/data/catalog_preview.json", {})
        trigger_data = prior.get("trigger_logic_generator", GeneratorOutput()).files.get("data/trigger_actions.json", {})
        damage_data = prior.get("realism_damage_generator", GeneratorOutput()).files.get("assets/data/damage_modifiers.json", {})

        channel_id = prior.get("tv_channel_generator", GeneratorOutput()).metadata.get("channel_id", "tv_shopping_network")
        channel_name = prior.get("tv_channel_generator", GeneratorOutput()).metadata.get("channel_name", "TV Shopping Network")
        mail_keys = mail_outputs.metadata.get("mail_keys", [])
        broadcast_key = mail_outputs.metadata.get("broadcast_key", "tv_shopping_broadcast")
        purchase_key = mail_outputs.metadata.get("purchase_key", "tv_shopping_delivery")
        catalogue_key = mail_outputs.metadata.get("catalogue_key")

        actions: list[dict] = []

        actions.append({
            "Action": "Load",
            "Target": f"mods/{mod_id}/tv_channels",
            "FromFile": "assets/data/tv_channels.json",
            "When": {"DayOfMonth": 6},
        })

        tv_channel_entry = {
            "Name": channel_name,
            "ChannelID": channel_id,
            "Description": "Weekly exclusive deals!",
            "IconSheet": "Television",
            "IconIndex": 0,
        }
        actions.append({
            "Action": "EditData",
            "Target": "Data/tvChannels",
            "Entries": {"tv_shopping_network": tv_channel_entry},
        })

        for mail_key in mail_keys:
            mail_file_name = f"mail/{mail_key}.json"
            if mail_file_name in mail_outputs.files:
                mail_content = mail_outputs.files[mail_file_name]
                if isinstance(mail_content, dict):
                    for letter_key, letter_text in mail_content.items():
                        mail_entry: dict = {"text": letter_text}
                        if letter_key == broadcast_key:
                            mail_entry["broadcast"] = True
                        if letter_key == purchase_key:
                            mail_entry["item"] = "%item.name%"
                            mail_entry["item_t"] = "Object"
                        actions.append({
                            "Action": "EditData",
                            "Target": "Data/mail",
                            "Entries": {letter_key: mail_entry},
                        })

        if shop_tsv and isinstance(shop_tsv, str):
            actions.append({
                "Action": "Load",
                "Target": f"Data/Shops/{mod_id}",
                "FromFile": "assets/data/shops.tsv",
                "When": {"DayOfMonth": 6},
            })

        if catalog_data:
            catalog_items = catalog_data.get("items", [])
            if catalog_items:
                actions.append({
                    "Action": "Load",
                    "Target": f"mods/{mod_id}/catalog_preview",
                    "FromFile": "assets/data/catalog_preview.json",
                    "When": {"MailReceived": broadcast_key},
                })

        if damage_data:
            dmg_multiplier = damage_data.get("DamageMultiplier", 1.0)
            price_scaling = damage_data.get("PriceScaling", {})
            if isinstance(price_scaling, dict) and price_scaling.get("enabled"):
                actions.append({
                    "Action": "Load",
                    "Target": f"mods/{mod_id}/damage_config",
                    "FromFile": "assets/data/damage_modifiers.json",
                })

        enabled = config_data.get("Enabled", True)
        if not enabled:
            actions = [{
                "Action": "EditData",
                "Target": "Data/tvChannels",
                "DeleteEntries": ["tv_shopping_network"],
            }]

        content_json: list[dict] = []
        for action in actions:
            content_action: dict = {"Action": action.pop("Action")}
            if action.get("Target"):
                content_action["Target"] = action.pop("Target")
            when_cond = action.pop("When", None)
            if when_cond:
                content_action["When"] = when_cond
            if action.get("FromFile"):
                content_action["FromFile"] = action.pop("FromFile")
            if action.get("Entries"):
                content_action["Entries"] = action.pop("Entries")
            if action.get("DeleteEntries"):
                content_action["DeleteEntries"] = action.pop("DeleteEntries")
            content_action.update(action)
            content_json.append(content_action)

        out.add_file("content.json", content_json)
        out.metadata["mod_id"] = mod_id
        out.metadata["actions_count"] = len(content_json)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("content_json_generator: content.json missing")
            return errors
        if not isinstance(content, list):
            errors.append("content_json_generator: content.json must be an array")
        else:
            for i, action in enumerate(content):
                if not isinstance(action, dict):
                    errors.append(f"content_json_generator: action[{i}] is not a dict")
                elif "Action" not in action:
                    errors.append(f"content_json_generator: action[{i}] missing 'Action' field")
        return errors