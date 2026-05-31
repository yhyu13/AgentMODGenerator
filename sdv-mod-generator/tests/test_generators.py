"""Generator tests — unit tests with mocked LLM calls."""

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.shop_channel import (
    ManifestGenerator,
    ShopItemPoolGenerator,
    TVChannelGenerator,
    MailSystemGenerator,
    ConfigSchemaGenerator,
    TriggerLogicGenerator,
)
from generators.packs.stardew_valley.features.texture import TextureGenerator


def make_input(prompt: str = "test prompt") -> GeneratorInput:
    return {
        "prompt": prompt,
        "hint": {},
        "request_id": "req_test",
        "game": "stardew_valley",
    }


class TestManifestGenerator:
    @pytest.mark.asyncio
    async def test_manifest_generator_fallback_no_llm(self):
        gen = ManifestGenerator()
        out = await gen.generate(make_input("tv shopping channel"))
        assert "manifest.json" in out.files
        manifest = out.files["manifest.json"]
        assert manifest["Format"] == "1.29.0"
        assert "UniqueID" in manifest
        assert manifest["ContentPackFor"]["UniqueID"] == "Pathoschild.ContentPatcher"

    @pytest.mark.asyncio
    async def test_manifest_generator_validate_passes(self):
        gen = ManifestGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_manifest_generator_validate_fails_on_missing(self):
        gen = ManifestGenerator()
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Name": "Only Name"})
        errors = gen.validate_output(out)
        assert any("UniqueID" in e for e in errors)


class TestShopItemPoolGenerator:
    @pytest.mark.asyncio
    async def test_shop_item_pool_fallback_no_llm(self):
        gen = ShopItemPoolGenerator()
        out = await gen.generate(make_input("shop with seeds"))
        assert "Data/Shops.tsv" in out.files
        tsv = out.files["Data/Shops.tsv"]
        assert "ItemType\tItemName\tItemName2\tPrice\tStock" in tsv
        assert "Parsnip" in tsv

    @pytest.mark.asyncio
    async def test_shop_tsv_has_header_and_rows(self):
        gen = ShopItemPoolGenerator()
        out = await gen.generate(make_input())
        tsv = out.files["Data/Shops.tsv"]
        lines = tsv.strip().split("\n")
        assert len(lines) >= 2
        assert lines[0] == "ItemType\tItemName\tItemName2\tPrice\tStock"

    @pytest.mark.asyncio
    async def test_validate_passes_with_tsv(self):
        gen = ShopItemPoolGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []


class TestTVChannelGenerator:
    @pytest.mark.asyncio
    async def test_tv_channel_fallback(self):
        gen = TVChannelGenerator()
        out = await gen.generate(make_input())
        assert "data/tv_channels.json" in out.files
        channels = out.files["data/tv_channels.json"]["channels"]
        assert len(channels) >= 1
        assert "Name" in channels[0]
        assert "ChannelID" in channels[0]


class TestMailSystemGenerator:
    @pytest.mark.asyncio
    async def test_mail_generator_fallback(self):
        gen = MailSystemGenerator()
        out = await gen.generate(make_input())
        mail_files = [f for f in out.files if f.startswith("mail/")]
        assert len(mail_files) >= 1
        mail_key = mail_files[0].split("/")[1].replace(".json", "")
        assert mail_key in out.files[mail_files[0]]

    @pytest.mark.asyncio
    async def test_validate_passes_with_mail(self):
        gen = MailSystemGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []


class TestConfigSchemaGenerator:
    @pytest.mark.asyncio
    async def test_config_schema_fallback(self):
        gen = ConfigSchemaGenerator()
        out = await gen.generate(make_input())
        assert "config.json" in out.files
        cfg = out.files["config.json"]
        assert "Enabled" in cfg
        assert "ShopDay" in cfg

    @pytest.mark.asyncio
    async def test_validate_passes(self):
        gen = ConfigSchemaGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []


class TestTriggerLogicGenerator:
    @pytest.mark.asyncio
    async def test_trigger_logic_fallback(self):
        gen = TriggerLogicGenerator()
        out = await gen.generate(make_input())
        assert "data/trigger_actions.json" in out.files
        triggers = out.files["data/trigger_actions.json"]
        assert "OnShopOpen" in triggers or "OnShopPurchase" in triggers

    @pytest.mark.asyncio
    async def test_validate_passes_with_triggers(self):
        gen = TriggerLogicGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []


class TestTextureGenerator:
    @pytest.mark.asyncio
    async def test_texture_generator_fallback(self):
        gen = TextureGenerator()
        out = await gen.generate(make_input("replace crop sprite"))
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert content["Format"] == "1.29.0"
        assert "Changes" in content

    @pytest.mark.asyncio
    async def test_texture_validate_passes(self):
        gen = TextureGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []


class TestGeneratorOutput:
    def test_add_file(self):
        out = GeneratorOutput()
        out.add_file("test.json", {"key": "value"})
        assert "test.json" in out.files
        assert out.files["test.json"] == {"key": "value"}

    def test_add_asset(self):
        out = GeneratorOutput()
        out.add_asset("assets/sprite.png")
        assert "assets/sprite.png" in out.assets

    def test_metadata(self):
        out = GeneratorOutput()
        out.metadata["mod_slug"] = "test_mod"
        assert out.metadata["mod_slug"] == "test_mod"
