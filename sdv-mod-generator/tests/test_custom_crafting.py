"""Tests for custom crafting and cooking recipe generators."""

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.custom_crafting import (
    CraftingRecipeGenerator,
    CookingRecipeGenerator,
    CraftingContentJsonGenerator,
)


def make_input(prompt: str = "test prompt") -> GeneratorInput:
    return {
        "prompt": prompt,
        "hint": {},
        "request_id": "req_test",
        "game": "stardew_valley",
        "prior_outputs": {},
        "t2_feedback": "",
    }


class TestCraftingRecipeGenerator:
    @pytest.mark.asyncio
    async def test_crafting_recipe_fallback_no_llm(self):
        gen = CraftingRecipeGenerator()
        out = await gen.generate(make_input("add custom crafting recipes"))
        assert "assets/data/crafting_recipes.json" in out.files
        data = out.files["assets/data/crafting_recipes.json"]
        assert "recipes" in data
        assert len(data["recipes"]) >= 1
        assert "RecipeName" in data["recipes"][0]
        assert "Ingredients" in data["recipes"][0]

    @pytest.mark.asyncio
    async def test_crafting_recipe_validate_passes(self):
        gen = CraftingRecipeGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_crafting_recipe_validate_fails_on_missing(self):
        gen = CraftingRecipeGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert len(errors) == 1
        assert "assets/data/crafting_recipes.json missing" in errors[0]

    @pytest.mark.asyncio
    async def test_crafting_recipe_metadata(self):
        gen = CraftingRecipeGenerator()
        out = await gen.generate(make_input())
        assert "recipe_count" in out.metadata
        assert out.metadata["recipe_count"] >= 1


class TestCookingRecipeGenerator:
    @pytest.mark.asyncio
    async def test_cooking_recipe_fallback_no_llm(self):
        gen = CookingRecipeGenerator()
        out = await gen.generate(make_input("add custom cooking recipes"))
        assert "assets/data/cooking_recipes.json" in out.files
        data = out.files["assets/data/cooking_recipes.json"]
        assert "recipes" in data
        assert len(data["recipes"]) >= 1
        assert "RecipeName" in data["recipes"][0]
        assert "Buffs" in data["recipes"][0]

    @pytest.mark.asyncio
    async def test_cooking_recipe_validate_passes(self):
        gen = CookingRecipeGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_cooking_recipe_validate_fails_on_missing(self):
        gen = CookingRecipeGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert len(errors) == 1
        assert "assets/data/cooking_recipes.json missing" in errors[0]

    @pytest.mark.asyncio
    async def test_cooking_recipe_metadata(self):
        gen = CookingRecipeGenerator()
        out = await gen.generate(make_input())
        assert "recipe_count" in out.metadata
        assert out.metadata["recipe_count"] >= 1


class TestCraftingContentJsonGenerator:
    @pytest.mark.asyncio
    async def test_content_json_with_prior_outputs(self):
        gen = CraftingContentJsonGenerator()
        inp = make_input("crafting mod")
        prior = GeneratorOutput()
        prior.add_file("assets/data/crafting_recipes.json", {
            "recipes": [
                {
                    "RecipeName": "Test_Bench",
                    "Description": "A test bench.",
                    "Ingredients": [{"ItemName": "Wood", "Quantity": 10}],
                    "OutputItem": "Test Bench",
                    "OutputQuantity": 1,
                    "SkillRequirement": None,
                }
            ]
        })
        prior.add_file("manifest.json", {
            "UniqueID": "Test.CraftingMod",
            "Format": "1.29.0",
        })
        inp["prior_outputs"] = {
            "crafting_recipe_generator": prior,
            "cooking_recipe_generator": GeneratorOutput(),
        }
        out = await gen.generate(inp)
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert content["Format"] == "1.29.0"
        assert "Changes" in content
        assert len(content["Changes"]) >= 1
        assert content["Changes"][0]["Action"] == "EditData"
        assert content["Changes"][0]["Target"] == "Data/CraftingRecipes"

    @pytest.mark.asyncio
    async def test_content_json_with_cooking(self):
        gen = CraftingContentJsonGenerator()
        inp = make_input("cooking mod")
        cooking_prior = GeneratorOutput()
        cooking_prior.add_file("assets/data/cooking_recipes.json", {
            "recipes": [
                {
                    "RecipeName": "Test_Soup",
                    "Description": "A test soup.",
                    "Ingredients": [{"ItemName": "Tomato", "Quantity": 2}],
                    "OutputItem": "Test Soup",
                    "OutputQuantity": 1,
                    "Buffs": {"Energy": 50},
                }
            ]
        })
        inp["prior_outputs"] = {
            "crafting_recipe_generator": GeneratorOutput(),
            "cooking_recipe_generator": cooking_prior,
        }
        out = await gen.generate(inp)
        content = out.files["content.json"]
        changes = content["Changes"]
        cooking_changes = [c for c in changes if c["Target"] == "Data/CookingRecipes"]
        assert len(cooking_changes) == 1
        assert "Test_Soup" in cooking_changes[0]["Entries"]

    @pytest.mark.asyncio
    async def test_content_json_validate_passes(self):
        gen = CraftingContentJsonGenerator()
        inp = make_input()
        inp["prior_outputs"] = {
            "crafting_recipe_generator": GeneratorOutput(),
            "cooking_recipe_generator": GeneratorOutput(),
        }
        out = await gen.generate(inp)
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_content_json_validate_fails_on_missing(self):
        gen = CraftingContentJsonGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert len(errors) == 1
        assert "content.json missing" in errors[0]

    @pytest.mark.asyncio
    async def test_content_json_mod_id_from_manifest(self):
        gen = CraftingContentJsonGenerator()
        inp = make_input("crafting mod")
        manifest_prior = GeneratorOutput()
        manifest_prior.add_file("manifest.json", {"UniqueID": "Author.CraftingMod"})
        inp["prior_outputs"] = {
            "manifest_generator": manifest_prior,
            "crafting_recipe_generator": GeneratorOutput(),
            "cooking_recipe_generator": GeneratorOutput(),
        }
        out = await gen.generate(inp)
        assert out.metadata["mod_id"] == "author.craftingmod"
