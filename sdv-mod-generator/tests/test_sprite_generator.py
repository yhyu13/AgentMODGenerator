"""Sprite generator tests: deterministic sample, mocked image flow, clear failure.

The image API is the I/O adapter — tests mock it or use the deterministic
sample (no network). The sprite post-processing itself is covered in
test_sprite_utils.py; these tests pin the generator's output contract.
"""
from __future__ import annotations

import pytest

from generators.core import GeneratorInput
from generators.packs.stardew_valley.features.sprite import SpriteGenerator
from generators.packs.stardew_valley.sprite_utils import decode_png


def _inp(prompt: str = "a glowing blue carp fish") -> GeneratorInput:
    return {
        "prompt": prompt,
        "hint": {"game": "stardew_valley", "phase": "sprite"},
        "request_id": "req_sprite",
        "game": "stardew_valley",
        "prior_outputs": {},
        "t2_feedback": "",
    }


class TestDeterministic:
    @pytest.mark.asyncio
    async def test_emits_16x16_sprite_and_valid_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SPRITE_DETERMINISTIC", "1")
        out = await SpriteGenerator().generate(_inp())

        png = out.files["Assets/sprite.png"]
        assert isinstance(png, bytes)
        pixels, width, height = decode_png(png)
        assert (width, height) == (16, 16)

        content = out.files["content.json"]
        assert content["Format"] == "2.0.0"
        change = content["Changes"][0]
        assert change["Action"] == "EditImage"
        assert change["FromFile"] == "Assets/sprite.png"
        # Modern CP 2.x field names (not the legacy SourceRect/ToRect).
        assert "FromArea" in change
        assert "ToArea" in change
        assert change["PatchMode"] == "Overlay"

        assert out.files["manifest.json"]["ContentPackFor"]["UniqueID"] == (
            "Pathoschild.ContentPatcher"
        )
        assert out.metadata.get("sprite") is True


class TestMockedImageFlow:
    @pytest.mark.asyncio
    async def test_mocked_image_flows_through_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        async def _fake(prompt: str):
            return sp._deterministic_pixels()

        monkeypatch.setattr(sp, "_generate_sprite_image", _fake)
        out = await sp.SpriteGenerator().generate(_inp("a glowing fish"))

        png = out.files["Assets/sprite.png"]
        pixels, width, height = decode_png(png)
        assert (width, height) == (16, 16)
        # The deterministic sample is a dark square on white; after
        # downsample+quantize the dark square survives as non-white pixels.
        assert any(c != (255, 255, 255) for c in pixels), (
            "sprite collapsed to all-background"
        )


class TestClearFailure:
    @pytest.mark.asyncio
    async def test_raises_clear_error_without_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        # conftest already unsets OPENAI_API_KEY, so no image provider.
        with pytest.raises(RuntimeError, match="image API key"):
            await SpriteGenerator().generate(_inp())
