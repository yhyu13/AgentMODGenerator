"""Custom crafting and cooking recipe generators for Stardew Valley.

Generates custom crafting recipes, cooking recipes, and assembles them
into a Content Patcher content.json.
"""
from pydantic import BaseModel, Field, ValidationError

import structlog
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger()


class CraftingIngredient(BaseModel):
    item_name: str = Field(validation_alias="ItemName")
    quantity: int = Field(default=1, validation_alias="Quantity")


class CraftingRecipeEntry(BaseModel):
    recipe_name: str = Field(validation_alias="RecipeName")
    description: str = Field(default="", validation_alias="Description")
    ingredients: list[CraftingIngredient] = Field(validation_alias="Ingredients")
    output_item: str = Field(validation_alias="OutputItem")
    output_quantity: int = Field(default=1, validation_alias="OutputQuantity")
    skill_requirement: str | None = Field(default=None, validation_alias="SkillRequirement")


class CraftingRecipeOutput(BaseModel):
    recipes: list[CraftingRecipeEntry] = Field(validation_alias="Recipes")


class CookingRecipeEntry(BaseModel):
    recipe_name: str = Field(validation_alias="RecipeName")
    description: str = Field(default="", validation_alias="Description")
    ingredients: list[CraftingIngredient] = Field(validation_alias="Ingredients")
    output_item: str = Field(validation_alias="OutputItem")
    output_quantity: int = Field(default=1, validation_alias="OutputQuantity")
    buffs: dict[str, int] | None = Field(default=None, validation_alias="Buffs")


class CookingRecipeOutput(BaseModel):
    recipes: list[CookingRecipeEntry] = Field(validation_alias="Recipes")


def _sanitize_recipe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c == "_") or "CustomRecipe"


class CraftingRecipeGenerator(BaseGenerator):
    name = "crafting_recipe_generator"
    phase = "custom_crafting"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create custom crafting recipes for Stardew Valley based on: "{inp["prompt"]}"

Generate 3-5 unique crafting recipes. For each recipe provide:
- RecipeName: snake_case identifier
- Description: 1 sentence
- Ingredients: list of {{"ItemName": "<sdv item>", "Quantity": <int>}}
- OutputItem: the resulting item name
- OutputQuantity: how many are produced (default 1)
- SkillRequirement: optional string like "Foraging 4" or "Mining 2"

Use only valid SDV item names. Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, CraftingRecipeOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            recipes = CraftingRecipeOutput(**result).recipes
            recipe_dicts = []
            for recipe in recipes:
                recipe_dicts.append({
                    "RecipeName": recipe.recipe_name,
                    "Description": recipe.description,
                    "Ingredients": [
                        {"ItemName": ing.item_name, "Quantity": ing.quantity}
                        for ing in recipe.ingredients
                    ],
                    "OutputItem": recipe.output_item,
                    "OutputQuantity": recipe.output_quantity,
                    "SkillRequirement": recipe.skill_requirement,
                })
            out.add_file("assets/data/crafting_recipes.json", {"recipes": recipe_dicts})
            out.metadata["recipe_count"] = len(recipe_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("crafting_recipe_generator.failed", error=str(exc))
            out.add_file("assets/data/crafting_recipes.json", {
                "recipes": [
                    {
                        "RecipeName": "Wooden_Bench",
                        "Description": "A simple wooden bench for your farm.",
                        "Ingredients": [{"ItemName": "Wood", "Quantity": 50}],
                        "OutputItem": "Wood Bench",
                        "OutputQuantity": 1,
                        "SkillRequirement": None,
                    },
                    {
                        "RecipeName": "Stone_Lamp",
                        "Description": "A stone lamp for outdoor lighting.",
                        "Ingredients": [{"ItemName": "Stone", "Quantity": 30}, {"ItemName": "Coal", "Quantity": 1}],
                        "OutputItem": "Stone Lamp",
                        "OutputQuantity": 1,
                        "SkillRequirement": "Mining 2",
                    },
                ]
            })
            out.metadata["recipe_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/crafting_recipes.json"):
            errors.append("crafting_recipe_generator: assets/data/crafting_recipes.json missing")
        return errors


class CookingRecipeGenerator(BaseGenerator):
    name = "cooking_recipe_generator"
    phase = "custom_crafting"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create custom cooking recipes for Stardew Valley based on: "{inp["prompt"]}"

Generate 2-4 unique cooking recipes. For each recipe provide:
- RecipeName: snake_case identifier
- Description: 1 sentence
- Ingredients: list of {{"ItemName": "<sdv item>", "Quantity": <int>}}
- OutputItem: the resulting dish name
- OutputQuantity: how many are produced (default 1)
- Buffs: optional dict like {{"Farming": 2, "Energy": 50}}

Use only valid SDV item names. Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, CookingRecipeOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            recipes = CookingRecipeOutput(**result).recipes
            recipe_dicts = []
            for recipe in recipes:
                recipe_dicts.append({
                    "RecipeName": recipe.recipe_name,
                    "Description": recipe.description,
                    "Ingredients": [
                        {"ItemName": ing.item_name, "Quantity": ing.quantity}
                        for ing in recipe.ingredients
                    ],
                    "OutputItem": recipe.output_item,
                    "OutputQuantity": recipe.output_quantity,
                    "Buffs": recipe.buffs or {},
                })
            out.add_file("assets/data/cooking_recipes.json", {"recipes": recipe_dicts})
            out.metadata["recipe_count"] = len(recipe_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("cooking_recipe_generator.failed", error=str(exc))
            out.add_file("assets/data/cooking_recipes.json", {
                "recipes": [
                    {
                        "RecipeName": "Farmers_Breakfast",
                        "Description": "A hearty breakfast to start the day.",
                        "Ingredients": [{"ItemName": "Egg", "Quantity": 1}, {"ItemName": "Milk", "Quantity": 1}],
                        "OutputItem": "Farmer's Breakfast",
                        "OutputQuantity": 1,
                        "Buffs": {"Farming": 2},
                    },
                    {
                        "RecipeName": "Berry_Smoothie",
                        "Description": "A refreshing berry smoothie.",
                        "Ingredients": [{"ItemName": "Blueberry", "Quantity": 2}, {"ItemName": "Milk", "Quantity": 1}],
                        "OutputItem": "Berry Smoothie",
                        "OutputQuantity": 1,
                        "Buffs": {"Energy": 50},
                    },
                ]
            })
            out.metadata["recipe_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/cooking_recipes.json"):
            errors.append("cooking_recipe_generator: assets/data/cooking_recipes.json missing")
        return errors


class CraftingContentJsonGenerator(BaseGenerator):
    name = "crafting_content_json_generator"
    phase = "custom_crafting"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        mod_id = manifest_data.get("UniqueID", "CustomCraftingMod").lower()

        crafting_gen = prior.get("crafting_recipe_generator", GeneratorOutput())
        cooking_gen = prior.get("cooking_recipe_generator", GeneratorOutput())

        changes: list[dict] = []

        crafting_file = "assets/data/crafting_recipes.json"
        if crafting_file in crafting_gen.files:
            crafting_data = crafting_gen.files[crafting_file]
            if isinstance(crafting_data, dict) and crafting_data.get("recipes"):
                for recipe in crafting_data["recipes"]:
                    recipe_name = recipe.get("RecipeName", "unknown")
                    changes.append({
                        "Action": "EditData",
                        "Target": "Data/CraftingRecipes",
                        "Entries": {
                            recipe_name: {
                                "Ingredients": recipe.get("Ingredients", []),
                                "OutputItem": recipe.get("OutputItem", ""),
                                "OutputQuantity": recipe.get("OutputQuantity", 1),
                                "SkillRequirement": recipe.get("SkillRequirement"),
                            }
                        },
                    })

        cooking_file = "assets/data/cooking_recipes.json"
        if cooking_file in cooking_gen.files:
            cooking_data = cooking_gen.files[cooking_file]
            if isinstance(cooking_data, dict):
                recipes_list = cooking_data.get("recipes", [])
                if isinstance(recipes_list, list):
                    for recipe in recipes_list:
                        if isinstance(recipe, dict):
                            recipe_name = recipe.get("RecipeName", "unknown")
                            changes.append({
                                "Action": "EditData",
                                "Target": "Data/CookingRecipes",
                                "Entries": {
                                    recipe_name: {
                                        "Ingredients": recipe.get("Ingredients", []),
                                        "OutputItem": recipe.get("OutputItem", ""),
                                        "OutputQuantity": recipe.get("OutputQuantity", 1),
                                        "Buffs": recipe.get("Buffs", {}),
                                    }
                                },
                            })

        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": changes,
        })
        out.metadata["mod_id"] = mod_id
        out.metadata["changes_count"] = len(changes)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("crafting_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("crafting_content_json_generator: content.json must be a dict")
        elif "Changes" not in content:
            errors.append("crafting_content_json_generator: Changes key missing")
        return errors
