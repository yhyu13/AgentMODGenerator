"""Texture replacement generator for Stardew Valley."""
import zlib

from pydantic import BaseModel

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from llm.client import get_client


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

        # Emit the fallback sprite the content.json references. The old
        # generator referenced @/assets/custom_sprite.png but never
        # produced it — SDV showed a missing-asset error at load time.
        out.add_file("assets/custom_sprite.png", _make_png(16, 16, 96, 128, 200))

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
        except (ValueError, RuntimeError, IOError):
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