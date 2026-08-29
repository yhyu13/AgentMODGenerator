"""AI sprite generator — turn a prompt into a 16×16 pixel-art sprite.

Pipeline: image API → decode → downsample → quantize → encode PNG →
EditImage into the mod. See doc/sprite-generator-plan.md.

The image API is the I/O adapter (mocked in tests); the deterministic
sprite post-processing lives in ``generators.packs.stardew_valley.sprite_utils``.
"""
from __future__ import annotations

import base64
import os

import structlog

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.core.manifest import build_manifest_dict, slugify_unique_id
from generators.packs.stardew_valley.sprite_utils import (
    decode_image,
    downsample,
    encode_png,
    quantize,
)

logger = structlog.get_logger(__name__)

SPRITE_TARGET = 16
SPRITE_PALETTE = 16


def _deterministic_pixels() -> tuple[list[tuple[int, int, int]], int, int]:
    """32×32 white image with a centered 16×16 dark square (no API needed)."""
    size = 32
    pixels = [
        (0, 0, 0) if 8 <= x < 24 and 8 <= y < 24 else (255, 255, 255)
        for y in range(size)
        for x in range(size)
    ]
    return pixels, size, size


def _sprite_prompt(prompt: str) -> str:
    """Shared pixel-art prompt for both image providers."""
    return (
        f"A 16x16 pixel art sprite of: {prompt}. Stardew Valley style, "
        "flat solid colors, limited palette, hard pixel edges, no "
        "anti-aliasing, centered on solid white background"
    )


async def _generate_openai_image(
    prompt: str,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """gpt-image-1.5 via the OpenAI-compatible ``/images/generations`` endpoint.

    Returns PNG (``data[0].b64_json``); decoded through :func:`decode_image`
    so a proxy that returns JPEG is handled identically.
    """
    import aiohttp

    key = os.environ.get("OPENAI_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not key:
        raise RuntimeError(
            "sprite requires an image API key (OPENAI_API_KEY) or "
            "SPRITE_DETERMINISTIC=1"
        )
    url = base.rstrip("/") + "/images/generations"
    payload = {
        "model": "gpt-image-1.5",
        "prompt": _sprite_prompt(prompt),
        "size": "1024x1024",
        "response_format": "b64_json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers={"Authorization": f"Bearer {key}"}
        ) as resp:
            data = await resp.json()
    if not data.get("data"):
        raise RuntimeError(f"sprite image API failed: {data}")
    b64 = data["data"][0].get("b64_json")
    if not b64:
        raise RuntimeError(
            f"sprite image API returned no b64_json: {list(data['data'][0])}"
        )
    return decode_image(base64.b64decode(b64))


async def _generate_minimax_image(
    prompt: str,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """MiniMax image-01 via ``/v1/image_generation`` (returns JPEG base64).

    The response wraps an application-level status in ``base_resp`` and the
    images under ``data.image_base64`` (a list, one per ``n``). The domain is
    ``api.minimaxi.com`` (not the docs' ``api.minimax.io``, which rejects the
    key with status 2049).
    """
    import aiohttp

    key = os.environ.get("MINIMAX_API_KEY", "")
    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    if not key:
        raise RuntimeError(
            "sprite minimax provider requires MINIMAX_API_KEY "
            "(or set SPRITE_IMAGE_PROVIDER=openai)"
        )
    url = base.rstrip("/") + "/image_generation"
    payload = {
        "model": "image-01",
        "prompt": _sprite_prompt(prompt),
        "width": 512,
        "height": 512,
        "response_format": "base64",
        "n": 1,
        "prompt_optimizer": False,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers={"Authorization": f"Bearer {key}"}
        ) as resp:
            data = await resp.json()
    base_resp = data.get("base_resp") or {}
    code = base_resp.get("status_code")
    if code not in (None, 0):
        raise RuntimeError(
            f"MiniMax image API error {code}: {base_resp.get('status_msg', 'unknown')}"
        )
    images = (data.get("data") or {}).get("image_base64") or []
    if not images:
        raise RuntimeError(
            f"MiniMax image API returned no image_base64: "
            f"{list((data.get('data') or {}).keys())}"
        )
    return decode_image(base64.b64decode(images[0]))


async def _generate_sprite_image(
    prompt: str,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """Call the image API and return (RGB pixels, width, height).

    Deterministic sample when ``SPRITE_DETERMINISTIC=1`` (tests / no-LLM
    gate); otherwise dispatch on ``SPRITE_IMAGE_PROVIDER``: ``openai``
    (default, gpt-image-1.5 → PNG) or ``minimax`` (image-01 → JPEG).
    """
    if os.environ.get("SPRITE_DETERMINISTIC") == "1":
        return _deterministic_pixels()

    provider = os.environ.get("SPRITE_IMAGE_PROVIDER", "openai").strip().lower()
    if provider == "minimax":
        return await _generate_minimax_image(prompt)
    return await _generate_openai_image(prompt)


class SpriteGenerator(BaseGenerator):
    name = "sprite_generator"
    phase = "sprite"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        pixels, width, height = await _generate_sprite_image(inp["prompt"])
        grid = downsample(pixels, width, height, target=SPRITE_TARGET)
        rgba_grid, _palette = quantize(grid, palette=SPRITE_PALETTE)
        png = encode_png(rgba_grid)

        unique_id = slugify_unique_id(inp["prompt"])
        out.add_file("Assets/sprite.png", png)
        out.add_file(
            "content.json",
            {
                "Format": "2.0.0",
                "Changes": [
                    {
                        "Action": "EditImage",
                        "Target": "Maps/springobjects",
                        "FromFile": "Assets/sprite.png",
                        "FromArea": {
                            "X": 0, "Y": 0,
                            "Width": SPRITE_TARGET, "Height": SPRITE_TARGET,
                        },
                        "ToArea": {
                            "X": 80, "Y": 96,
                            "Width": SPRITE_TARGET, "Height": SPRITE_TARGET,
                        },
                        "PatchMode": "Overlay",
                    }
                ],
            },
        )
        out.add_file(
            "manifest.json",
            build_manifest_dict(
                unique_id,
                "Sprite Mod",
                f"A sprite generated from: {inp['prompt'][:140]}",
            ),
        )
        out.metadata["sprite"] = True
        logger.info(
            "sprite_generator.done",
            request_id=inp["request_id"],
            sprite_size=f"{SPRITE_TARGET}x{SPRITE_TARGET}",
        )
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        if "Assets/sprite.png" not in output.files:
            errors.append("sprite_generator: Assets/sprite.png missing")
        content = output.files.get("content.json")
        if not isinstance(content, dict):
            errors.append("sprite_generator: content.json missing")
        elif not isinstance(content.get("Changes"), list) or not content["Changes"]:
            errors.append("sprite_generator: content.json missing 'Changes'")
        return errors
