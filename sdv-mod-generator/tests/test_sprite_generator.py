"""Sprite generator tests: deterministic sample, mocked image flow, clear failure.

The image API is the I/O adapter — tests mock it or use the deterministic
sample (no network). The sprite post-processing itself is covered in
test_sprite_utils.py; these tests pin the generator's output contract.
"""
from __future__ import annotations

import pytest

from generators.core import GeneratorInput
from generators.packs.stardew_valley.features.sprite import SpriteGenerator
from generators.packs.stardew_valley.sprite_utils import decode_png, encode_png


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


class TestMiniMaxProvider:
    """MiniMax image-01 branch: request shape + response parsing (mocked I/O)."""

    @pytest.mark.asyncio
    async def test_minimax_branch_parses_image_base64(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        import aiohttp

        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

        # A tiny valid PNG so decode_image sniffs the PNG path (pure stdlib,
        # no Pillow needed in this test).
        png = encode_png([[(1, 2, 3, 255)]])
        b64 = base64.b64encode(png).decode()

        captured: dict = {}

        class _FakeResp:
            async def json(self):
                return {
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                    "data": {"image_base64": [b64]},
                    "id": "trace-123",
                }

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return _FakeResp()

        monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)

        pixels, width, height = await sp._generate_minimax_image("a fish")

        assert (width, height) == (1, 1)
        assert pixels == [(1, 2, 3)]
        assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
        assert captured["json"]["model"] == "image-01"
        assert captured["json"]["width"] == 512
        assert captured["json"]["height"] == 512
        assert captured["json"]["response_format"] == "base64"
        assert captured["json"]["prompt_optimizer"] is False
        assert captured["headers"]["Authorization"] == "Bearer test-minimax-key"

    @pytest.mark.asyncio
    async def test_minimax_error_status_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import aiohttp

        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.setenv("MINIMAX_API_KEY", "k")

        class _FakeResp:
            async def json(self):
                return {
                    "base_resp": {"status_code": 2049, "status_msg": "invalid api key"}
                }

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def post(self, *a, **kw):
                return _FakeResp()

        monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)
        with pytest.raises(RuntimeError, match="2049"):
            await sp._generate_minimax_image("a fish")

    @pytest.mark.asyncio
    async def test_provider_dispatch_minimax(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        monkeypatch.setenv("SPRITE_IMAGE_PROVIDER", "minimax")

        async def _fake_minimax(prompt):
            return [(9, 9, 9)], 1, 1

        monkeypatch.setattr(sp, "_generate_minimax_image", _fake_minimax)
        pixels, w, h = await sp._generate_sprite_image("a fish")
        assert (w, h) == (1, 1)
        assert pixels == [(9, 9, 9)]


class TestProviderAutoDetect:
    """SPRITE_IMAGE_PROVIDER unset → provider inferred from the backend.

    MiniMax's OpenAI-compatible endpoint serves chat (M2.7), not
    gpt-image-1.5, so a MiniMax base URL (or a present MINIMAX_API_KEY)
    must route to the minimax provider instead of the broken openai path.
    """

    @pytest.mark.asyncio
    async def test_minimax_base_url_selects_minimax(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        monkeypatch.delenv("SPRITE_IMAGE_PROVIDER", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")

        async def _fake_minimax(prompt):
            return [(1, 2, 3)], 1, 1

        monkeypatch.setattr(sp, "_generate_minimax_image", _fake_minimax)
        pixels, w, h = await sp._generate_sprite_image("a fish")
        assert (w, h) == (1, 1)
        assert pixels == [(1, 2, 3)]

    @pytest.mark.asyncio
    async def test_minimax_key_selects_minimax(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        monkeypatch.delenv("SPRITE_IMAGE_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "k")

        async def _fake_minimax(prompt):
            return [(4, 5, 6)], 1, 1

        monkeypatch.setattr(sp, "_generate_minimax_image", _fake_minimax)
        pixels, w, h = await sp._generate_sprite_image("a fish")
        assert pixels == [(4, 5, 6)]

    @pytest.mark.asyncio
    async def test_openai_base_url_selects_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        monkeypatch.delenv("SPRITE_IMAGE_PROVIDER", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        async def _fake_openai(prompt):
            return [(7, 8, 9)], 1, 1

        monkeypatch.setattr(sp, "_generate_openai_image", _fake_openai)
        pixels, w, h = await sp._generate_sprite_image("a fish")
        assert pixels == [(7, 8, 9)]

    @pytest.mark.asyncio
    async def test_explicit_provider_overrides_auto_detect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.sprite as sp

        monkeypatch.delenv("SPRITE_DETERMINISTIC", raising=False)
        monkeypatch.setenv("SPRITE_IMAGE_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")

        async def _fake_openai(prompt):
            return [(0, 0, 0)], 1, 1

        monkeypatch.setattr(sp, "_generate_openai_image", _fake_openai)
        pixels, w, h = await sp._generate_sprite_image("a fish")
        assert pixels == [(0, 0, 0)]
