"""Texture replacement generator for Stardew Valley."""
from pydantic import BaseModel

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from llm.client import get_client


class _SourceRect(BaseModel):
    x: int
    y: int
    width: int
    height: int


class _TextureSpec(BaseModel):
    sprite_sheet: str
    source_rect: _SourceRect
    target_file: str
    target_rect: _SourceRect


class TextureGenerator(BaseGenerator):
    name = "texture_generator"
    phase = "texture"
    game = "stardew_valley"

    SYSTEM_PROMPT = """You are a texture replacement expert for Stardew Valley.
Given an object name or sprite description, return:
1. The correct sprite sheet (e.g., springobjects, fruitTrees, etc.)
2. The source rectangle coordinates (x, y, width, height)
3. The target edit action

Respond with valid JSON."""

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        llm_prompt = f'User wants to replace texture: {inp["prompt"]}\nReturn JSON with sprite_sheet, source_rect, target_file, target_rect.'

        try:
            client = get_client()
            result = await client.complete_with_structured_output(
                prompt=llm_prompt,
                output_schema=_TextureSpec,
                system=self.SYSTEM_PROMPT,
            )
            spec = _TextureSpec(**result)
            sprite_sheet = spec.sprite_sheet
            source_rect = {"X": spec.source_rect.x, "Y": spec.source_rect.y,
                           "Width": spec.source_rect.width, "Height": spec.source_rect.height}
            target_file = spec.target_file
            target_rect = {"X": spec.target_rect.x, "Y": spec.target_rect.y,
                           "Width": spec.target_rect.width, "Height": spec.target_rect.height}

            out.add_file("content.json", {
                "Format": "1.29.0",
                "Changes": [
                    {
                        "Action": "EditImage",
                        "Target": f"@/{sprite_sheet}",
                        "FromFile": f"@/assets/{target_file}",
                        "SourceRect": source_rect,
                        "ToRect": target_rect,
                    }
                ],
            })
        except Exception:
            out.add_file("content.json", {
                "Format": "1.29.0",
                "Changes": [
                    {
                        "Action": "EditImage",
                        "Target": "@/Maps/springobjects",
                        "FromFile": "@/assets/custom_sprite.png",
                        "SourceRect": {"X": 0, "Y": 0, "Width": 16, "Height": 16},
                        "ToRect": {"X": 80, "Y": 96, "Width": 16, "Height": 16},
                    }
                ],
            })
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("texture_generator: content.json missing")
            return errors
        if not content.get("Changes"):
            errors.append("texture_generator: no changes in content.json")
        return errors