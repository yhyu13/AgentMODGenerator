"""P1 shop channel generators — stubs."""
from generators.base import BaseGenerator, GeneratorInput, GeneratorOutput


class ManifestGenerator(BaseGenerator):
    name = "manifest_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("manifest.json", {
            "Format": "1.29.0",
            "ConfigSchema": {
                "Enabled": {"AllowValues": ["true", "false"], "Default": "true"},
                "DiscountRate": {"AllowValues": ["0.5", "0.75", "1.0"], "Default": "1.0"},
            },
            "UniqueID": "TVShoppingNetwork",
            "Name": "TV Shopping Network",
            "Author": "AI Generator",
            "Version": "1.0.0",
            "Description": "A TV shopping channel that sells random items every Sunday morning.",
            "ContentPackFor": {
                "UniqueID": "Pathoschild.ContentPatcher",
            },
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        manifest = output.files.get("manifest.json")
        if not manifest:
            errors.append("manifest_generator: manifest.json missing")
            return errors
        required = ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]
        for field in required:
            if field not in manifest:
                errors.append(f"manifest_generator: missing required field '{field}'")
        return errors


class ShopItemPoolGenerator(BaseGenerator):
    name = "shop_item_pool_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        shop_items = [
            {"ItemType": "Object", "ItemName": "Parsnip", "Price": 50},
            {"ItemType": "Object", "ItemName": "Melon", "Price": 250},
            {"ItemType": "Object", "ItemName": "Pumpkin", "Price": 300},
            {"ItemType": "Object", "ItemName": "Crystalarium", "Price": 750},
            {"ItemType": "Object", "ItemName": "Auto-Grabber", "Price": 1200},
            {"ItemType": "Object", "ItemName": "Mega Bomb", "Price": 100},
            {"ItemType": "Object", "ItemName": "Deluxe Speed-Gro", "Price": 40},
            {"ItemType": "Object", "ItemName": "Quality Fertilizer", "Price": 10},
            {"ItemType": "BigCraftable", "ItemName": "Scarecrow", "Price": 200},
            {"ItemType": "Object", "ItemName": "Brick Floor", "Price": 1},
        ]
        out.add_file("Data/Shops.tsv", self._build_shop_tsv(shop_items))
        return out

    def _build_shop_tsv(self, items: list[dict]) -> str:
        header = "ItemType\tItemName\tItemName2\tPrice\tStackSize"
        rows = [header]
        for item in items:
            name2 = item.get("ItemName2", "")
            rows.append(f"{item['ItemType']}\t{item['ItemName']}\t{name2}\t{item['Price']}\t1")
        return "\n".join(rows)

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if "Data/Shops.tsv" not in output.files:
            errors.append("shop_item_pool_generator: Data/Shops.tsv missing")
        return errors


class TVChannelGenerator(BaseGenerator):
    name = "tv_channel_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("data/tv_channes.json", {
            "channels": [
                {
                    "Name": "TV Shopping Network",
                    "ChannelID": "shopping",
                    "Description": "Daily shopping deals delivered to your TV!",
                    "IconSheet": "Television",
                    "IconIndex": 0,
                }
            ]
        })
        out.add_file("mail/tv_shopping_broadcast.json", {
            "broadcasts": [
                {
                    "day": "Sunday",
                    "mail": "tv_shopping_broadcast",
                    "channel": "TV Shopping Network",
                }
            ]
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class MailSystemGenerator(BaseGenerator):
    name = "mail_system_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("mail/tv_shopping_broadcast.json", {
            "tv_shopping_broadcast": "Dear @, ^The TV Shopping Network is broadcasting today! Check your local TV for special deals. Come visit us at the shop anytime between 6am and 10pm on Sunday.^  - Your TV Shopping Network",
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        mail = output.files.get("mail/tv_shopping_broadcast.json", {})
        if "tv_shopping_broadcast" not in mail:
            errors.append("mail_system_generator: tv_shopping_broadcast mail missing")
        return errors


class ItemSpritesGenerator(BaseGenerator):
    name = "item_sprites_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("assets/sprites/shop_logo.json", {
            "SourceRect": {"X": 0, "Y": 0, "Width": 32, "Height": 32},
            "SpriteSheet": "Maps/springobjects",
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class UIAssetsGenerator(BaseGenerator):
    name = "ui_assets_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
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

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("catalog_preview.json", {
            "shop_name": "TV Shopping Network",
            "broadcast_day": "Sunday",
            "items": [
                {"name": "Parsnip Seeds", "price": 50, "description": "Grows quickly, great for beginners."},
                {"name": "Melon Seeds", "price": 250, "description": "Sells for a high price in summer."},
                {"name": "Crystalarium", "price": 750, "description": "Generates gems over time."},
                {"name": "Auto-Grabber", "price": 1200, "description": "Collects products from farm animals automatically."},
            ],
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class RealismDamageGenerator(BaseGenerator):
    name = "realism_damage_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("data/damage_modifiers.json", {
            "ModID": "TVShoppingNetwork",
            "DamageMultiplier": 1.0,
            "PriceScaling": {
                "enabled": True,
                "factor": 1.2,
            },
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        return []


class TriggerLogicGenerator(BaseGenerator):
    name = "trigger_logic_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        out.add_file("data/trigger_actions.json", {
            "OnShopOpen": [
                {"Action": "Mail", "Mail": "tv_shopping_broadcast"},
            ],
            "OnShopPurchase": [
                {"Action": "RemoveItem", "Item": "GoldCoin", "Amount": 1},
                {"Action": "PlaySound", "Sound": "purchase"},
            ],
        })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        triggers = output.files.get("data/trigger_actions.json", {})
        if "OnShopOpen" not in triggers and "OnShopPurchase" not in triggers:
            errors.append("trigger_logic_generator: at least one trigger expected")
        return errors


class ConfigSchemaGenerator(BaseGenerator):
    name = "config_schema_generator"
    phase = "p1_shop_channel"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
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
