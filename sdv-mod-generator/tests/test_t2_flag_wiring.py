"""t2_three_judge_panel feature flag wiring tests.

Pins the claude audit §1.4 fix: the flag was defined and toggled in tests
but never read by ``quality.gate_t2``. Now ``_run_judge_panel`` consults
the flag — flag off = single technical-compliance judge; flag on = the
full 3-judge panel. The quorum math also adapts to a reduced panel.
"""
from __future__ import annotations

import asyncio

import orchestrator.feature_flags as feature_flags_module
from generators.core import GeneratorOutput
from quality import gate_t2


def _make_outputs() -> dict:
    out = GeneratorOutput()
    out.add_file("manifest.json", {"Format": "1.29.0", "Name": "X", "UniqueID": "a.b", "Version": "1.0.0"})
    return {"manifest_generator": out}


class _FakeClient:
    def __init__(self) -> None:
        self.complete = None  # never used — _llm_judge is monkeypatched


async def _fake_judge(request_id, summary, client, persona):
    return 8, "fine", True


async def _fake_judge_fail(request_id, summary, client, persona):
    return 3, "bad", False


def test_panel_flag_on_runs_three_judges(monkeypatch):
    monkeypatch.setattr(feature_flags_module, "is_enabled", lambda name: True)
    monkeypatch.setattr(gate_t2, "_llm_judge", _fake_judge)

    async def scenario() -> list:
        return await gate_t2._run_judge_panel("req_t2", _make_outputs(), _FakeClient())

    panel = asyncio.run(scenario())
    assert len(panel) == 3
    assert {r.judge_name for r in panel} == {
        "GameBalanceJudge", "ContentQualityJudge", "TechnicalComplianceJudge",
    }


def test_panel_flag_off_runs_single_judge(monkeypatch):
    monkeypatch.setattr(feature_flags_module, "is_enabled", lambda name: False)
    monkeypatch.setattr(gate_t2, "_llm_judge", _fake_judge)

    async def scenario() -> list:
        return await gate_t2._run_judge_panel("req_t2", _make_outputs(), _FakeClient())

    panel = asyncio.run(scenario())
    assert len(panel) == 1
    assert panel[0].judge_name == "TechnicalComplianceJudge"


def test_run_t2_single_judge_pass_quorum(monkeypatch):
    """With a 1-judge panel, one passing judge passes the gate (not 2-of-3)."""
    monkeypatch.setattr(feature_flags_module, "is_enabled", lambda name: False)
    monkeypatch.setattr(gate_t2, "_llm_judge", _fake_judge)
    monkeypatch.setattr(gate_t2, "get_client", lambda: _FakeClient())

    result = asyncio.run(gate_t2.run_t2("req_t2", _make_outputs()))
    assert result.available is True
    assert result.passed is True
    assert result.score == 8


def test_run_t2_single_judge_fail_quorum(monkeypatch):
    monkeypatch.setattr(feature_flags_module, "is_enabled", lambda name: False)
    monkeypatch.setattr(gate_t2, "_llm_judge", _fake_judge_fail)
    monkeypatch.setattr(gate_t2, "get_client", lambda: _FakeClient())

    result = asyncio.run(gate_t2.run_t2("req_t2", _make_outputs()))
    assert result.available is True
    assert result.passed is False
    assert result.score == 3
