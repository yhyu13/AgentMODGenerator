"""Schema-level tests for the Session 2 prompt-keyed estimate schemas.

Companion to the v55 schema port — pins the Pydantic contract that the
``GET /v1/estimate`` and ``POST /v1/estimate/batch`` routes (next
round, v56) will emit. Schema-only (no TestClient) because the route
handlers depend on ``app.estimation`` and ``orchestrator.router.route``
which are not exercised in this round; the schemas themselves have
zero runtime dependency on those modules (the docstring references
are text only). Mirrors the v33 (schema) → v34 (handler + handler
tests) split and the v54 (estimate response schemas) → v55 (prompt
schemas) split.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    BatchPromptEstimateItem,
    BatchPromptEstimateRequest,
    BatchPromptEstimateResponse,
    PromptEstimateResponse,
)


class TestPromptEstimateResponse:
    """``PromptEstimateResponse`` is the ``/v1/estimate`` envelope."""

    def test_matched_true_round_trip(self) -> None:
        # The canonical-table hit case — prompt resolves to a known
        # phase, ``seconds`` is the phase-specific estimate, ``matched``
        # is True, ``prompt`` echoes back.
        r = PromptEstimateResponse(
            prompt="make a TV shopping channel",
            phase="shop_channel",
            seconds=30,
            default_seconds=90,
            matched=True,
        )
        assert r.prompt == "make a TV shopping channel"
        assert r.phase == "shop_channel"
        assert r.seconds == 30
        assert r.default_seconds == 90
        assert r.matched is True
        # ``game`` defaults to ``stardew_valley`` — pins the fallback
        # pack the router uses when no keyword matches.
        assert r.game == "stardew_valley"

    def test_matched_false_round_trip(self) -> None:
        # The graceful-degrade case — prompt does not resolve to a
        # known phase, ``seconds == default_seconds``, ``matched`` is
        # False. ``phase`` echoes the resolved (fallback) phase id.
        r = PromptEstimateResponse(
            prompt="something obscure",
            phase="custom_crafting",
            seconds=90,
            default_seconds=90,
            matched=False,
        )
        assert r.prompt == "something obscure"
        assert r.seconds == 90
        assert r.matched is False

    def test_game_can_be_overridden(self) -> None:
        # ``game`` has a default but is not frozen — a future pack
        # registration could resolve to a different game. Pin the
        # override path.
        r = PromptEstimateResponse(
            prompt="x",
            phase="y",
            seconds=1,
            default_seconds=90,
            matched=False,
            game="haunted_chocolatier",
        )
        assert r.game == "haunted_chocolatier"


class TestBatchPromptEstimateItem:
    """``BatchPromptEstimateItem`` is one row of the batch response."""

    def test_minimal_round_trip(self) -> None:
        item = BatchPromptEstimateItem(
            phase="shop_channel",
            seconds=30,
            default_seconds=90,
            matched=True,
        )
        assert item.phase == "shop_channel"
        assert item.seconds == 30
        assert item.default_seconds == 90
        assert item.matched is True
        # ``game`` default mirrors ``PromptEstimateResponse``.
        assert item.game == "stardew_valley"

    def test_seconds_must_be_ge_1(self) -> None:
        # Same rationale as ``PhaseEstimate.seconds``.
        BatchPromptEstimateItem(
            phase="shop_channel", seconds=1, default_seconds=90, matched=True
        )  # boundary: ok
        with pytest.raises(ValidationError):
            BatchPromptEstimateItem(
                phase="shop_channel", seconds=0, default_seconds=90, matched=True
            )


class TestBatchPromptEstimateRequest:
    """``BatchPromptEstimateRequest`` is the ``POST /v1/estimate/batch`` body."""

    def test_minimal_round_trip(self) -> None:
        req = BatchPromptEstimateRequest(prompts=["a", "b", "c"])
        assert req.prompts == ["a", "b", "c"]

    def test_prompts_are_trimmed(self) -> None:
        # Each prompt is trimmed at the schema boundary — the leading/
        # trailing whitespace is dropped before the handler sees it.
        req = BatchPromptEstimateRequest(prompts=["  hello  ", "world"])
        assert req.prompts == ["hello", "world"]

    def test_empty_prompt_after_trim_rejected(self) -> None:
        # A prompt that is whitespace-only becomes empty after trim
        # and is rejected — same hygiene as ``GenerateRequest``.
        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=["valid", "   "])

    def test_null_byte_rejected(self) -> None:
        # Same null-byte guard as ``GenerateRequest._validate_prompt``.
        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=["valid\x00prompt"])

    def test_min_length_one_enforced(self) -> None:
        # ``min_length=1`` on the list field — an empty batch is a 422.
        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=[])

    def test_max_length_twenty_enforced(self) -> None:
        # ``max_length=20`` on the list field — a 21-prompt batch is a 422.
        with pytest.raises(ValidationError):
            BatchPromptEstimateRequest(prompts=["p"] * 21)


class TestBatchPromptEstimateResponse:
    """``BatchPromptEstimateResponse`` is the batch envelope."""

    def test_round_trip(self) -> None:
        items = [
            BatchPromptEstimateItem(
                phase="shop_channel",
                seconds=30,
                default_seconds=90,
                matched=True,
            ),
            BatchPromptEstimateItem(
                phase="custom_crafting",
                seconds=90,
                default_seconds=90,
                matched=False,
            ),
        ]
        resp = BatchPromptEstimateResponse(
            estimates=items, count=2, default_seconds=90
        )
        assert resp.estimates == items
        assert resp.count == 2
        assert resp.default_seconds == 90

    def test_empty_batch_response(self) -> None:
        # Defensive shape — even though the request layer rejects an
        # empty batch, the response schema accepts an empty list
        # (count=0, default_seconds>0) so a route can construct one
        # in a unit test without bypassing the validator.
        resp = BatchPromptEstimateResponse(
            estimates=[], count=0, default_seconds=90
        )
        assert resp.estimates == []
        assert resp.count == 0

    def test_default_seconds_must_be_ge_1(self) -> None:
        # Same rationale as ``EstimatesResponse.default_seconds``.
        BatchPromptEstimateResponse(
            estimates=[], count=0, default_seconds=1
        )  # boundary: ok
        with pytest.raises(ValidationError):
            BatchPromptEstimateResponse(
                estimates=[], count=0, default_seconds=0
            )