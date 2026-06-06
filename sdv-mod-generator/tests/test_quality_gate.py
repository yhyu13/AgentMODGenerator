"""Quality gate tests — T1 deterministic and T2 LLM judge."""
import pytest

from generators.core import GeneratorOutput
from quality.gate_t1 import run_t1, T1Result
from quality.gate_t2 import run_t2, T2Result


class TestT1Gate:
    def test_t1_passes_valid_manifest(self, sample_manifest):
        out = GeneratorOutput()
        out.add_file("manifest.json", sample_manifest)
        result = run_t1("req_test", {"manifest_generator": out})
        assert result.passed is True
        assert result.errors == []

    def test_t1_fails_missing_unique_id(self, malformed_manifest):
        out = GeneratorOutput()
        out.add_file("manifest.json", malformed_manifest)
        result = run_t1("req_test", {"manifest_generator": out})
        assert result.passed is False
        assert any("UniqueID" in e for e in result.errors)

    def test_t1_fails_empty_shops_tsv(self):
        out = GeneratorOutput()
        out.add_file("assets/data/shops.tsv", "")
        result = run_t1("req_test", {"shop_item_pool_generator": out})
        assert result.passed is False
        assert any("no data rows" in e for e in result.errors)

    def test_t1_passes_valid_shops_tsv(self, sample_shops_tsv):
        out = GeneratorOutput()
        out.add_file("assets/data/shops.tsv", sample_shops_tsv)
        result = run_t1("req_test", {"shop_item_pool_generator": out})
        assert result.passed is True
        assert result.errors == []

    def test_t1_passes_config_schema(self):
        out = GeneratorOutput()
        out.add_file("config.json", {"Enabled": True})
        result = run_t1("req_test", {"config_schema_generator": out})
        assert result.passed is True

    def test_t1_fails_missing_config_enabled(self):
        out = GeneratorOutput()
        out.add_file("config.json", {"ShopDay": 0})
        result = run_t1("req_test", {"config_schema_generator": out})
        assert result.passed is False
        assert any("Enabled" in e for e in result.errors)

    def test_t1_passes_trigger_logic(self):
        out = GeneratorOutput()
        out.add_file("data/trigger_actions.json", {
            "OnShopOpen": [{"Action": "Mail", "Mail": "test"}],
        })
        result = run_t1("req_test", {"trigger_logic_generator": out})
        assert result.passed is True

    def test_t1_fails_empty_trigger_logic(self):
        out = GeneratorOutput()
        out.add_file("data/trigger_actions.json", {})
        result = run_t1("req_test", {"trigger_logic_generator": out})
        assert result.passed is False

    def test_t1_result_dataclass(self):
        result = T1Result(passed=True, errors=[])
        assert result.passed is True
        assert result.errors == []


class TestT2Gate:
    @pytest.mark.asyncio
    async def test_t2_no_llm_returns_skipped(self):
        result = await run_t2("req_test", {})
        assert result.passed is True
        assert result.score == 0
        assert "no LLM" in result.feedback

    def test_t2_result_dataclass(self):
        result = T2Result(available=True, passed=True, score=8, feedback="good mod")
        assert result.passed is True
        assert result.score == 8
        assert result.feedback == "good mod"
        assert result.available is True

    def test_parse_strips_think_block_before_verdict(self):
        from quality.gate_t2 import _parse_judge_response
        response = (
            "<think>The mod has a TV channel that sells seeds. "
            "It looks well-balanced. SCORE: 8 FEEDBACK: solid concept</think>\n"
            "SCORE: 8\n"
            "FEEDBACK: Solid concept with good balance and clear pricing."
        )
        score, feedback = _parse_judge_response(response)
        assert score == 8, f"expected score 8, got {score}"
        assert "Solid concept" in feedback

    def test_parse_handles_single_line_response(self):
        from quality.gate_t2 import _parse_judge_response
        response = "SCORE: 7 FEEDBACK: Decent implementation"
        score, feedback = _parse_judge_response(response)
        assert score == 7
        assert "Decent" in feedback

    def test_parse_fallback_to_bare_number(self):
        from quality.gate_t2 import _parse_judge_response
        response = (
            "<think>I'll give this a 6. The structure is okay but the "
            "balance could use work.</think>\n"
            "Final score: 6 — decent but unbalanced."
        )
        score, feedback = _parse_judge_response(response)
        assert score == 6, f"expected fallback score 6, got {score}"
        assert "decent" in feedback.lower() or "unbalanced" in feedback.lower()

    def test_parse_returns_zero_when_no_signal(self):
        from quality.gate_t2 import _parse_judge_response
        response = "No relevant content here"
        score, feedback = _parse_judge_response(response)
        assert score == 0
