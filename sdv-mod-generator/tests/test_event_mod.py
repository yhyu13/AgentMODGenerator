"""Tests for the event_mod festival generators."""

import pytest
from generators.packs.stardew_valley.features.event_mod import (
    FestivalScheduleGenerator,
    FestivalShopGenerator,
    FestivalMapGenerator,
    FestivalDialogueGenerator,
    FestivalMailGenerator,
    FestivalContentJsonGenerator,
)
from generators.core.base import GeneratorInput, GeneratorOutput


def build_festival_input() -> GeneratorInput:
    """Build a fully-populated festival prior-output input."""
    schedule_out = GeneratorOutput()
    schedule_out.add_file("assets/festivals/SpringFlower_schedule.json", {"name": "SpringFlower"})
    schedule_out.metadata["festival_name"] = "SpringFlower"
    schedule_out.metadata["season"] = "spring"
    schedule_out.metadata["day"] = 13

    shop_out = GeneratorOutput()
    shop_out.add_file("assets/festivals/SpringFlower_shop.json", {"shop_name": "SpringFlower Shop"})

    map_out = GeneratorOutput()
    map_out.add_file("assets/festivals/SpringFlower_map.json", {"map_name": "Town"})

    dialogue_out = GeneratorOutput()
    dialogue_out.add_file("assets/festivals/SpringFlower_dialogue.json", {"festival_abigail": "Hi!"})

    mail_out = GeneratorOutput()
    mail_out.add_file("mail/springflower_announcement.json", {"springflower_announcement": "Come join us!"})
    mail_out.metadata["mail_key"] = "springflower_announcement"

    return GeneratorInput(
        prompt="Create a spring flower festival",
        prior_outputs={
            "festival_schedule_generator": schedule_out,
            "festival_shop_generator": shop_out,
            "festival_map_generator": map_out,
            "festival_dialogue_generator": dialogue_out,
            "festival_mail_generator": mail_out,
        },
    )


class TestFestivalScheduleGenerator:
    """Tests for FestivalScheduleGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = FestivalScheduleGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "festival_name" in out.metadata
        assert "season" in out.metadata
        assert "day" in out.metadata

    def test_validate_output_detects_missing_file(self):
        gen = FestivalScheduleGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("no schedule file" in e for e in errors)

    def test_validate_output_passes_with_file(self):
        gen = FestivalScheduleGenerator()
        out = GeneratorOutput()
        out.add_file("assets/festivals/SpringFlower_schedule.json", {"name": "SpringFlower"})
        errors = gen.validate_output(out)
        assert not errors


class TestFestivalShopGenerator:
    """Tests for FestivalShopGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = FestivalShopGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "festival_name" in out.metadata
        assert "shop_items" in out.metadata

    def test_validate_output_detects_missing_file(self):
        gen = FestivalShopGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("no shop file" in e for e in errors)


class TestFestivalMapGenerator:
    """Tests for FestivalMapGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = FestivalMapGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "festival_name" in out.metadata
        assert "npc_count" in out.metadata

    def test_validate_output_detects_missing_file(self):
        gen = FestivalMapGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("no map file" in e for e in errors)


class TestFestivalDialogueGenerator:
    """Tests for FestivalDialogueGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = FestivalDialogueGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "festival_name" in out.metadata
        assert "dialogue_count" in out.metadata

    def test_validate_output_detects_missing_file(self):
        gen = FestivalDialogueGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("no dialogue file" in e for e in errors)


class TestFestivalMailGenerator:
    """Tests for FestivalMailGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = FestivalMailGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "festival_name" in out.metadata
        assert "mail_key" in out.metadata

    def test_validate_output_detects_missing_file(self):
        gen = FestivalMailGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("no announcement mail" in e for e in errors)


class TestFestivalContentJsonGenerator:
    """Tests for FestivalContentJsonGenerator."""

    @pytest.mark.asyncio
    async def test_generate_assembles_content(self):
        gen = FestivalContentJsonGenerator()
        inp = GeneratorInput(prompt="Create a spring flower festival")
        # Set up prior outputs
        schedule_out = GeneratorOutput()
        schedule_out.add_file("assets/festivals/SpringFlower_schedule.json", {"name": "SpringFlower"})
        schedule_out.metadata["festival_name"] = "SpringFlower"
        schedule_out.metadata["season"] = "spring"
        schedule_out.metadata["day"] = 13

        shop_out = GeneratorOutput()
        shop_out.add_file("assets/festivals/SpringFlower_shop.json", {"shop_name": "SpringFlower Shop"})

        map_out = GeneratorOutput()
        map_out.add_file("assets/festivals/SpringFlower_map.json", {"map_name": "Town"})

        dialogue_out = GeneratorOutput()
        dialogue_out.add_file("assets/festivals/SpringFlower_dialogue.json", {"festival_abigail": "Hi!"})

        mail_out = GeneratorOutput()
        mail_out.add_file("mail/springflower_announcement.json", {"springflower_announcement": "Come join us!"})
        mail_out.metadata["mail_key"] = "springflower_announcement"

        inp = GeneratorInput(
            prompt="Create a spring flower festival",
            prior_outputs={
                "festival_schedule_generator": schedule_out,
                "festival_shop_generator": shop_out,
                "festival_map_generator": map_out,
                "festival_dialogue_generator": dialogue_out,
                "festival_mail_generator": mail_out,
            },
        )
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert isinstance(content, dict)
        assert "Changes" in content
        assert len(content["Changes"]) > 0

    @pytest.mark.asyncio
    async def test_festival_data_patches_use_load(self):
        gen = FestivalContentJsonGenerator()
        out = await gen.generate(build_festival_input())
        content = out.files["content.json"]
        load_targets = []
        for change in content["Changes"]:
            if "FromFile" in change:
                assert change["Action"] == "Load"
                load_targets.append(change["Target"])
            elif change["Action"] == "EditData":
                assert "FromFile" not in change
        assert len(load_targets) == 4

    def test_validate_output_detects_missing_content(self):
        gen = FestivalContentJsonGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("content.json missing" in e for e in errors)
