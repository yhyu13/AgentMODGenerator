"""General-author generator tests: deterministic sample, LLM path, fallback.

The general author is the hybrid 'unbounded' layer — a single LLM-driven
generator that handles any prompt outside the 10 template phases. These
tests pin the deterministic sample path (no LLM keys), the mocked-LLM
path, and the clear-failure fallback, plus T1/static validation of the
emitted content.
"""
from __future__ import annotations

import pytest

from generators.core import GeneratorInput
from generators.packs.stardew_valley.features.general_author import (
    GeneralAuthorGenerator,
)
from quality.gate_t1 import run_t1


def _inp(prompt: str = "add a custom fish that glows") -> GeneratorInput:
    return {
        "prompt": prompt,
        "hint": {"game": "stardew_valley", "phase": "general_author"},
        "request_id": "req_gen_author",
        "game": "stardew_valley",
        "prior_outputs": {},
        "t2_feedback": "",
    }


class TestDeterministicSample:
    @pytest.mark.asyncio
    async def test_emits_valid_manifest_and_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENERAL_AUTHOR_DETERMINISTIC", "1")
        out = await GeneralAuthorGenerator().generate(_inp())
        manifest = out.files["manifest.json"]
        content = out.files["content.json"]
        assert manifest["ContentPackFor"]["UniqueID"] == "Pathoschild.ContentPatcher"
        assert content["Format"] == "2.0.0"
        assert content["Changes"][0]["Action"] == "EditData"
        assert out.metadata.get("general_author") is True

    @pytest.mark.asyncio
    async def test_sample_passes_t1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENERAL_AUTHOR_DETERMINISTIC", "1")
        out = await GeneralAuthorGenerator().generate(_inp())
        result = run_t1("req_gen_author", {"general_author_generator": out})
        assert result.passed, result.errors


class TestLlmPath:
    @pytest.mark.asyncio
    async def test_uses_llm_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canned = {
            "Name": "Glowing Fish",
            "Description": "A fish that glows in the dark.",
            "content": {
                "Format": "2.0.0",
                "Changes": [
                    {"Action": "EditData", "Target": "Data/Fish",
                     "Entries": {"GlowingFish": "100/Flamboyant/summer/freshwater/sunny/12:00/22:00/1/5/10/100"}},
                ],
            },
        }
        import generators.packs.stardew_valley.features.general_author as ga

        async def _fake_generate_structured(prompt, output_schema, system=None, max_tokens=2048, max_retries=2, base_delay=1.0):
            return canned

        monkeypatch.setattr(ga, "generate_structured", _fake_generate_structured)
        out = await ga.GeneralAuthorGenerator().generate(_inp())
        content = out.files["content.json"]
        assert content["Changes"][0]["Target"] == "Data/Fish"
        assert out.files["manifest.json"]["Name"] == "Glowing Fish"
        result = run_t1("req_gen_author", {"general_author_generator": out})
        assert result.passed, result.errors

    @pytest.mark.asyncio
    async def test_raises_clear_error_when_llm_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import generators.packs.stardew_valley.features.general_author as ga

        async def _boom(prompt, output_schema, system=None, max_tokens=2048, max_retries=2, base_delay=1.0):
            raise RuntimeError("No LLM provider configured")

        monkeypatch.setattr(ga, "generate_structured", _boom)
        with pytest.raises(RuntimeError, match="requires an LLM provider"):
            await ga.GeneralAuthorGenerator().generate(_inp())


class TestValidateOutput:
    def test_missing_files_rejected(self) -> None:
        from generators.base import GeneratorOutput

        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0"})
        errors = GeneralAuthorGenerator().validate_output(out)
        assert any("content.json missing" in e for e in errors)


class TestEmbeddedDataSchemas:
    def test_schema_section_embeds_verified_assets(self) -> None:
        from generators.packs.stardew_valley.features.general_author import (
            _data_schemas_section,
        )

        section = _data_schemas_section()
        # The verified shapes that the real-game gate caught as LLM mistakes
        # must be present so the model stops guessing them from memory.
        assert "Data/Achievements" in section
        assert "Data/Objects" in section
        assert "Data/Locations" in section
        assert "Integer" in section or "integer" in section  # Fields-index rule
        assert "2.0.0" in section  # Format rule

    def test_schema_section_renders_into_system_prompt(self) -> None:
        from generators.packs.stardew_valley.features.general_author import (
            _general_author_system_prompt,
        )

        prompt = _general_author_system_prompt()
        assert "Data/Achievements" in prompt
        assert "VERIFIED" in prompt
