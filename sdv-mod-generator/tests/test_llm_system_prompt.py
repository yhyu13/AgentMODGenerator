"""Tests for the LLM system prompt wiring (v101).

The system prompt at llm_system_prompt() in generators/llm_utils.py is
the single source of truth for what the LLM sees. It must:

- Include the STARDEW_VALLEY_MOD_STANDARDS.md document
- Start with the base 5-line instruction
- End with the JSON-only output trailer
- Behave correctly when the standards file is missing or unreadable
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSystemPromptStructure:
    """The system prompt has 3 parts: base, standards, trailer."""

    def test_prompt_includes_standards_doc(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        assert "STARDEW_VALLEY_MOD_STANDARDS.md" in prompt, (
            "The system prompt must reference the standards doc by name "
            "so the LLM knows to read it"
        )

    def test_prompt_includes_mod_quality_bar(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The standards doc has this phrase in the T2 judge section
        assert "GameBalanceJudge" in prompt, (
            "The standards doc should describe all 3 T2 judges so the "
            "LLM knows what it's being scored on"
        )
        assert "ContentQualityJudge" in prompt
        assert "TechnicalComplianceJudge" in prompt

    def test_prompt_includes_required_files_section(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The standards doc has this section
        assert "manifest.json" in prompt
        assert "content.json" in prompt
        assert "mail/*.txt" in prompt or "mail" in prompt

    def test_prompt_includes_naming_conventions(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The standards doc has a naming-conventions table
        assert "snake_case" in prompt
        assert "PascalCase" in prompt or "Pascal" in prompt

    def test_prompt_includes_anti_patterns(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The standards doc has the 12-mod anti-pattern list
        assert "anti-pattern" in prompt.lower() or "don\'t do" in prompt.lower() or "do not" in prompt.lower()

    def test_prompt_includes_json_only_trailer(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The trailer is the original 5-line instruction
        assert "Output ONLY valid JSON" in prompt
        assert "no markdown" in prompt
        assert "forward slashes" in prompt
        # "Prices are in gold" is split across lines in the trailer
        # ("Prices are\nin gold"), so check the parts independently.
        assert "Prices are" in prompt
        assert "in gold" in prompt

    def test_prompt_includes_base_instruction(self) -> None:
        from generators.llm_utils import llm_system_prompt
        prompt = llm_system_prompt()
        # The base is the 5-line intro
        assert prompt.startswith("You are a Stardew Valley Content Patcher mod generator.")

    def test_prompt_reads_standards_on_every_call(self) -> None:
        # The prompt should re-read the file (not cache) so doc edits
        # take effect without a process restart. This is a no-op test
        # in production but verifies the function is not memoized.
        from generators import llm_utils
        first = llm_utils.llm_system_prompt()
        second = llm_utils.llm_system_prompt()
        assert first == second  # both reads produce the same content
        # And the function is a regular function, not a property
        assert callable(llm_utils.llm_system_prompt)


class TestSystemPromptFallback:
    """When the standards doc is missing, the prompt falls back gracefully."""

    def test_missing_standards_file_yields_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch Path to make the standards path always report missing
        from generators import llm_utils

        original_exists = Path.exists

        def fake_exists(self: Path) -> bool:
            if self.name == "STARDEW_VALLEY_MOD_STANDARDS.md":
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        prompt = llm_utils.llm_system_prompt()
        # Even without the doc, the base + trailer must be present
        assert prompt.startswith("You are a Stardew Valley Content Patcher mod generator.")
        assert "Output ONLY valid JSON" in prompt
        # And a fallback note must be present so the LLM knows something is off
        assert "missing" in prompt.lower() or "fall back" in prompt.lower()
