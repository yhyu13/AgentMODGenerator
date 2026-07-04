"""Tests for ``GET /v1/mods/{request_id}/t2_judges``.

Round v52: ports the route handler from the discord-ops-hardening
branch (source: ``docs/_source_routes_app_api.py.txt`` lines
2130-2356 — the ``get_mod_t2_judges`` handler plus the
``_T2_JUDGES_MAX_ITERATIONS`` constant and the
``_build_t2_judges_from_redis`` helper).

The endpoint exposes the per-iteration T2 judge history so operators
and chat bots can render "what did each T2 retry see?" without
re-parsing the full status payload. The handler:

1. **Redis live state.** If ``get_pipeline_state`` returns a dict,
   the handler builds a ``T2JudgesResponse`` from it directly.
   ``iterations`` comes from ``t2_judge_results`` (defensively
   validated entry-by-entry — bad entries are skipped with WARNING,
   not raised); ``final_score`` / ``final_passed`` / ``t2_available``
   are echoed from the top-level Redis fields.
2. **DB fallback.** If Redis is cold, the handler falls back to
   ``get_mod_output`` to confirm existence. ``iterations`` is empty
   (per-iteration history is Redis-only); ``final_score`` /
   ``final_passed`` / ``t2_available`` are read from the DB row.
   The response is 200 with ``source="db_unavailable"`` so the
   caller knows the request exists but the history has expired.
3. **404 not found.** If both Redis is cold AND the DB row is
   missing, the handler returns 404. Unlike ``/timeline`` which
   also 404s on dual-miss, ``/t2_judges`` distinguishes "request
   never ran T2" (200, source="redis", empty list) from "request
   existed but Redis expired" (200, source="db_unavailable", empty
   list) from "request never existed" (404).

A transient Redis / DB error on either path is logged and treated
as a miss — the fallback path is attempted before the 404 is
returned. Programming bugs (TypeError, KeyError) still propagate
so they aren't masked as transient outages.

Tests use ``patch.object`` on:

- ``app.api.routes.get_mod_output`` (module-level import)
- ``storage.redis.get_pipeline_state`` (imported inside the handler,
  so the source-module attribute is the right patch target)

The deferred-import pattern is why the tests use ``patch.object``
on the source module rather than on the already-imported name in
``app.api.routes`` — the local binding inside the handler is fresh
on every call.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.api.routes as routes_module
import storage.redis as redis_module
from app.api.routes import (
    _T2_JUDGES_MAX_ITERATIONS,
    _build_t2_judges_from_redis,
    get_mod_t2_judges,
)
from app.api.schemas import T2JudgeIteration, T2JudgesResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_query(output, side_effect=None):
    """Patch ``app.api.routes.get_mod_output`` to return ``output``.

    ``side_effect`` is accepted so a test can simulate a transient
    DB error on the ``get_mod_output`` call.
    """
    mock = AsyncMock(return_value=output, side_effect=side_effect)
    return patch.object(routes_module, "get_mod_output", mock), mock


def _patch_redis_state(state, side_effect=None):
    """Patch ``storage.redis.get_pipeline_state``.

    ``get_pipeline_state`` is imported inside ``get_mod_t2_judges``,
    so the source-module attribute is the right patch target.
    Returns ``(patch_ctx, mock)``.
    """
    mock = AsyncMock(return_value=state, side_effect=side_effect)
    return patch.object(redis_module, "get_pipeline_state", mock), mock


def _make_iteration(index: int, *, score: int | None = 8, passed: bool = True) -> dict:
    """Build a Redis-shaped ``t2_judge_results`` entry."""
    return {
        "iteration": index,
        "score": score,
        "feedback": f"iter {index} feedback",
        "passed": passed,
        "panel_scores": [score] if score is not None else [],
        "panel_passed_count": 1 if (score is not None and score >= 7) else 0,
    }


# ---------------------------------------------------------------------------
# Happy path: Redis live state with a populated t2_judge_results
# ---------------------------------------------------------------------------


class TestT2JudgesEndpointRedisLive:
    """Endpoint shape when ``get_pipeline_state`` returns a populated dict."""

    async def test_returns_iterations_from_redis(self):
        """Live Redis state with 2 iterations → 200 with both iterations echoed."""
        redis_state = {
            "status": "done",
            "t2_judge_results": [_make_iteration(1), _make_iteration(2)],
            "t2_score": 9,
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-1")

        assert isinstance(response, T2JudgesResponse)
        assert response.request_id == "req-1"
        assert len(response.iterations) == 2
        assert response.iterations[0].iteration == 1
        assert response.iterations[1].iteration == 2
        assert response.iterations[0].score == 8
        assert response.final_score == 9
        assert response.final_passed is True
        assert response.t2_available is True
        assert response.source == "redis"

    async def test_returns_empty_iterations_when_t2_never_ran(self):
        """Redis hit but ``t2_judge_results`` is ``[]`` → 200, empty list, source='redis'."""
        redis_state = {
            "status": "done",
            "t2_judge_results": [],
            "t2_score": None,
            "t2_passed": None,
            "t2_available": False,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-empty")

        assert response.iterations == []
        assert response.final_score is None
        assert response.final_passed is None
        assert response.t2_available is False
        # source='redis' because the request DID exist in Redis — just
        # with an empty history.
        assert response.source == "redis"

    async def test_skips_non_dict_entries_with_warning(self):
        """Malformed non-dict entries are skipped, not raised."""
        redis_state = {
            "t2_judge_results": [
                _make_iteration(1),
                "not a dict",  # skipped
                42,             # skipped
                _make_iteration(2),
            ],
            "t2_score": 8,
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-malformed")

        assert len(response.iterations) == 2
        assert response.iterations[0].iteration == 1
        assert response.iterations[1].iteration == 2
        assert response.source == "redis"

    async def test_skips_pydantic_failing_entries(self):
        """Entries that fail Pydantic validation are skipped."""
        redis_state = {
            "t2_judge_results": [
                _make_iteration(1),
                {  # missing required 'iteration', 'passed'
                    "score": 7,
                    "feedback": "no iteration field",
                },
                _make_iteration(2),
            ],
            "t2_score": 7,
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-bad-pydantic")

        assert len(response.iterations) == 2
        assert response.iterations[0].iteration == 1
        assert response.iterations[1].iteration == 2

    async def test_truncates_at_max_iterations(self):
        """More iterations than the cap → cap is enforced with WARNING."""
        n = _T2_JUDGES_MAX_ITERATIONS + 5
        redis_state = {
            "t2_judge_results": [_make_iteration(i + 1) for i in range(n)],
            "t2_score": 8,
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-many")

        assert len(response.iterations) == _T2_JUDGES_MAX_ITERATIONS
        assert response.iterations[0].iteration == 1
        assert response.iterations[-1].iteration == _T2_JUDGES_MAX_ITERATIONS

    async def test_final_score_clamped_to_zero_ten(self):
        """Out-of-range ``t2_score`` values are clamped (defensive)."""
        redis_state = {
            "t2_judge_results": [],
            "t2_score": 999,  # → 10
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-clamp-high")
        assert response.final_score == 10

        redis_state["t2_score"] = -50  # → 0
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-clamp-low")
        assert response.final_score == 0

    async def test_final_score_none_when_not_int(self):
        """``t2_score`` that isn't coercible to int → final_score=None."""
        redis_state = {
            "t2_judge_results": [],
            "t2_score": "not a number",
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-bad-score")
        assert response.final_score is None

    async def test_final_passed_none_when_not_bool(self):
        """``t2_passed`` that isn't a bool → final_passed=None."""
        redis_state = {
            "t2_judge_results": [],
            "t2_score": 8,
            "t2_passed": "yes",  # not a bool
            "t2_available": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-bad-passed")
        assert response.final_passed is None

    async def test_t2_available_false_when_missing_or_non_bool(self):
        """``t2_available`` defaults to False when missing or non-bool."""
        redis_state = {
            "t2_judge_results": [],
            "t2_score": 8,
            "t2_passed": True,
        }
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-no-avail")
        assert response.t2_available is False

        redis_state["t2_available"] = "yes"  # non-bool → False
        with _patch_redis_state(redis_state)[0], _patch_query(None)[0]:
            response = await get_mod_t2_judges("req-bad-avail")
        assert response.t2_available is False


# ---------------------------------------------------------------------------
# DB fallback: Redis miss but request exists in DB
# ---------------------------------------------------------------------------


class TestT2JudgesEndpointDbFallback:
    """Endpoint shape when Redis is cold but ``get_mod_output`` returns a row."""

    async def test_returns_db_unavailable_with_final_fields(self):
        """Redis miss + DB row → 200, source='db_unavailable', iterations=[]."""
        db_output = {
            "request_id": "req-2",
            "t2_score": 7,
            "t2_passed": True,
            "t2_available": True,
        }
        with _patch_redis_state(None)[0], _patch_query(db_output)[0]:
            response = await get_mod_t2_judges("req-2")

        assert response.iterations == []
        assert response.final_score == 7
        assert response.final_passed is True
        assert response.t2_available is True
        assert response.source == "db_unavailable"

    async def test_db_row_with_no_t2_fields(self):
        """DB row without t2_* fields → empty defaults, source='db_unavailable'."""
        db_output = {"request_id": "req-3"}
        with _patch_redis_state(None)[0], _patch_query(db_output)[0]:
            response = await get_mod_t2_judges("req-3")

        assert response.iterations == []
        assert response.final_score is None
        assert response.final_passed is None
        assert response.t2_available is False
        assert response.source == "db_unavailable"


# ---------------------------------------------------------------------------
# 404 path: Redis miss AND DB row missing
# ---------------------------------------------------------------------------


class TestT2JudgesEndpointNotFound:
    """Endpoint raises 404 when both Redis and DB have no record."""

    async def test_redis_none_and_db_none_raises_404(self):
        """Redis miss + DB miss → HTTPException 404."""
        with _patch_redis_state(None)[0], _patch_query(None)[0]:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_t2_judges("req-missing")

        assert exc_info.value.status_code == 404
        assert "req-missing" in str(exc_info.value.detail)

    async def test_redis_none_and_db_empty_dict_raises_404(self):
        """Redis miss + DB returning ``{}`` (treated as missing) → 404."""
        with _patch_redis_state(None)[0], _patch_query({})[0]:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_t2_judges("req-empty-db")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Transient error handling: Redis/DB transient errors fall through
# ---------------------------------------------------------------------------


class TestT2JudgesEndpointTransientErrors:
    """Transient errors on Redis or DB are logged and treated as a miss."""

    async def test_redis_timeout_falls_through_to_db(self):
        """Redis TimeoutError → DB hit → 200 source='db_unavailable'."""
        db_output = {
            "request_id": "req-r1",
            "t2_score": 6,
            "t2_passed": False,
            "t2_available": True,
        }
        with _patch_redis_state(None, side_effect=asyncio.TimeoutError("redis slow"))[0], \
             _patch_query(db_output)[0]:
            response = await get_mod_t2_judges("req-r1")

        assert response.source == "db_unavailable"
        assert response.final_score == 6
        assert response.final_passed is False

    async def test_redis_connection_error_falls_through_to_db(self):
        """Redis ConnectionError → DB hit → 200 source='db_unavailable'."""
        db_output = {"t2_score": 5, "t2_passed": False, "t2_available": True}
        with _patch_redis_state(None, side_effect=ConnectionError("redis down"))[0], \
             _patch_query(db_output)[0]:
            response = await get_mod_t2_judges("req-r2")

        assert response.source == "db_unavailable"
        assert response.final_score == 5

    async def test_redis_runtime_error_falls_through_to_db(self):
        """Redis RuntimeError → DB hit → 200 source='db_unavailable'."""
        db_output = {"t2_score": 8, "t2_passed": True, "t2_available": True}
        with _patch_redis_state(None, side_effect=RuntimeError("redis weird"))[0], \
             _patch_query(db_output)[0]:
            response = await get_mod_t2_judges("req-r3")

        assert response.source == "db_unavailable"

    async def test_db_timeout_after_redis_miss_raises_404(self):
        """Redis miss + DB TimeoutError → 404 (output treated as None)."""
        with _patch_redis_state(None)[0], \
             _patch_query(None, side_effect=asyncio.TimeoutError("db slow"))[0]:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_t2_judges("req-d1")
        assert exc_info.value.status_code == 404

    async def test_db_connection_error_after_redis_miss_raises_404(self):
        """Redis miss + DB ConnectionError → 404."""
        with _patch_redis_state(None)[0], \
             _patch_query(None, side_effect=ConnectionError("db down"))[0]:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_t2_judges("req-d2")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Helper unit tests: _build_t2_judges_from_redis (pure transformation)
# ---------------------------------------------------------------------------


class TestBuildT2JudgesFromRedisHelper:
    """Direct unit tests for the pure helper, no I/O involved."""

    def test_none_redis_state_returns_source_none(self):
        """``redis_state=None`` → empty response, source='none'."""
        response = _build_t2_judges_from_redis("req-h1", None)
        assert response.request_id == "req-h1"
        assert response.iterations == []
        assert response.final_score is None
        assert response.final_passed is None
        assert response.t2_available is False
        assert response.source == "none"

    def test_empty_dict_redis_state_returns_source_none(self):
        """``redis_state={}`` → empty response, source='none'."""
        response = _build_t2_judges_from_redis("req-h2", {})
        assert response.iterations == []
        assert response.source == "none"

    def test_missing_t2_judge_results_key_returns_empty(self):
        """``t2_judge_results`` missing → empty list, not raised."""
        response = _build_t2_judges_from_redis(
            "req-h3", {"t2_score": 5}
        )
        assert response.iterations == []
        assert response.final_score == 5
        assert response.source == "redis"

    def test_non_list_t2_judge_results_returns_empty(self):
        """``t2_judge_results`` wrong type → empty list, not raised."""
        response = _build_t2_judges_from_redis(
            "req-h4", {"t2_judge_results": "not a list"}
        )
        assert response.iterations == []

    def test_normal_construction(self):
        """Happy-path helper construction."""
        redis_state = {
            "t2_judge_results": [_make_iteration(1)],
            "t2_score": 9,
            "t2_passed": True,
            "t2_available": True,
        }
        response = _build_t2_judges_from_redis("req-h5", redis_state)
        assert len(response.iterations) == 1
        assert response.final_score == 9
        assert response.final_passed is True
        assert response.source == "redis"


# ---------------------------------------------------------------------------
# Schema contract tests: Pydantic model invariants
# ---------------------------------------------------------------------------


class TestT2JudgesResponseSchema:
    """Pydantic-level guards on the response models."""

    def test_minimal_constructor_round_trips(self):
        """``T2JudgesResponse(request_id=...)`` → default source='none'."""
        response = T2JudgesResponse(request_id="req-s1")
        assert response.request_id == "req-s1"
        assert response.iterations == []
        assert response.final_score is None
        assert response.final_passed is None
        assert response.t2_available is False
        assert response.source == "none"

    def test_source_must_be_literal(self):
        """``source`` rejects values outside the Literal set."""
        with pytest.raises(Exception):  # ValidationError
            T2JudgesResponse(request_id="req-s2", source="unknown")

    def test_iteration_must_be_positive(self):
        """``iteration`` enforces ``ge=1``."""
        with pytest.raises(Exception):  # ValidationError
            T2JudgeIteration(iteration=0, passed=True, panel_passed_count=0)

    def test_score_must_be_in_range(self):
        """``score`` enforces ``ge=0, le=10``."""
        with pytest.raises(Exception):  # ValidationError
            T2JudgeIteration(iteration=1, passed=True, panel_passed_count=0, score=11)
        with pytest.raises(Exception):
            T2JudgeIteration(iteration=1, passed=True, panel_passed_count=0, score=-1)

    def test_panel_passed_count_must_be_non_negative(self):
        """``panel_passed_count`` enforces ``ge=0``."""
        with pytest.raises(Exception):
            T2JudgeIteration(iteration=1, passed=True, panel_passed_count=-1)

    def test_response_json_round_trip(self):
        """Full response round-trips through model_dump_json without losing fields."""
        response = T2JudgesResponse(
            request_id="req-s3",
            iterations=[T2JudgeIteration(iteration=1, score=9, passed=True, panel_passed_count=1)],
            final_score=9,
            final_passed=True,
            t2_available=True,
            source="redis",
        )
        json_str = response.model_dump_json()
        parsed = T2JudgesResponse.model_validate_json(json_str)
        assert parsed.request_id == "req-s3"
        assert len(parsed.iterations) == 1
        assert parsed.iterations[0].score == 9
        assert parsed.final_score == 9
        assert parsed.final_passed is True
        assert parsed.t2_available is True
        assert parsed.source == "redis"