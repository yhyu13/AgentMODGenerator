"""Tests for POST /v1/mods/generate/batch.

Complements ``tests/test_batch_api.py`` (4 cases) with loop-internal
contract pins. Per iteration, the endpoint does the same five-step dance
as ``generate_mod`` (unique id, ``create_mod_request``, ``redis_set_status``
with ``\"running\"``, ``run_pipeline_background``, ``BatchGenerateItem``
append) -- closure-capture, single-iteration, or str-dedupe bugs would
show up here.

We call the function directly (bypassing FastAPI DI / response_model
serialisation) to assert the raw ``BatchGenerateResponse`` shape.
"""
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.schemas import BatchGenerateRequest
from app.api.routes import generate_mod_batch


BATCH_ID_RE = re.compile(r"^batch_[0-9a-f]{12}$")
REQ_ID_RE = re.compile(r"^req_[0-9a-f]{12}$")


class TestBatchGenerateEndpointIdContract:
    """Pins: batch_id and per-item request_id shape."""

    async def test_batch_id_matches_batch_12hex_format(self):
        """batch_id must be ``batch_<12 hex>`` -- pin both prefix and length."""
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        req = BatchGenerateRequest(user_id="u1", prompts=["alpha"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            result = await generate_mod_batch(req)

        assert BATCH_ID_RE.match(result.batch_id), (
            f"batch_id {result.batch_id!r} does not match {BATCH_ID_RE.pattern}"
        )
    async def test_per_item_request_ids_are_unique_and_12hex(self):
        """Each iteration must produce a fresh ``req_<12 hex>`` id.

        The ``uuid.uuid4().hex[:12]`` slice inside the loop is the easy
        place to introduce a closure bug; pin uniqueness here.
        """
        prompts = ["a", "b", "c", "d"]
        req = BatchGenerateRequest(user_id="u1", prompts=prompts)
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            result = await generate_mod_batch(req)

        rids = [item.request_id for item in result.items]
        assert all(REQ_ID_RE.match(r) for r in rids), (
            f"non-matching request_ids: {rids}"
        )
        assert len(set(rids)) == len(rids), (
            f"duplicate request_ids in batch: {rids}"
        )
        assert result.batch_id not in rids


class TestBatchGenerateEndpointLoopArity:
    """Pins 2, 3, 4, 5: each downstream callable fires exactly N times
    with the right args."""

    async def test_create_mod_request_called_once_per_prompt_with_phase_batch(self):
        """create_mod_request awaited N times, each with phase='batch'.

        Differs from single-shot's 'p1_shop_channel' -- str-dedupe
        refactor would surface as a phase mismatch.
        """
        prompts = ["x", "y", "z"]
        req = BatchGenerateRequest(user_id="user-7", prompts=prompts)
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            await generate_mod_batch(req)

        assert create.await_count == len(prompts)
        for call in create.await_args_list:
            args = call.args
            assert len(args) == 6, f"create_mod_request called with {len(args)} args, expected 6"
            assert args[1] == "user-7", "user_id not threaded through"
            assert args[3] == "batch", f"expected phase='batch', got phase={args[3]!r}"
            assert args[4] == [] and args[5] == {}, "trailing defaults must be [] and {}"

    async def test_run_pipeline_background_invoked_once_per_item(self):
        """``run_pipeline_background`` is sync (not async) — called N times."""
        prompts = ["one", "two"]
        req = BatchGenerateRequest(user_id="u3", prompts=prompts)
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            await generate_mod_batch(req)

        assert bg.call_count == len(prompts)
        for call, prompt in zip(bg.call_args_list, prompts):
            args = call.args
            assert len(args) == 3
            assert args[1] == "u3"
            assert args[2] == prompt

    async def test_redis_set_status_runs_before_pipeline_per_iteration(self):
        """Per iteration, ``redis_set_status`` is awaited BEFORE
        ``run_pipeline_background`` is called -- avoids race-creating the
        cache key during the pipeline's first mutation.
        """
        prompts = ["p1", "p2"]
        req = BatchGenerateRequest(user_id="u", prompts=prompts)
        order: list[str] = []
        async def mock_create(*args, **kwargs):
            order.append("create")
        async def mock_set_status(rid, status):
            order.append("set_status")
        def mock_bg(*args, **kwargs):
            order.append("bg")

        create = AsyncMock(side_effect=mock_create)
        set_status = AsyncMock(side_effect=mock_set_status)
        bg = MagicMock(side_effect=mock_bg)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            await generate_mod_batch(req)

        for i in range(len(prompts)):
            assert order[i * 3 : i * 3 + 3] == ["create", "set_status", "bg"]


class TestBatchGenerateEndpointResponseShape:
    """Pin: per-item prompt + estimated_seconds reflects _estimate_seconds."""

    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("replace a sprite", 30),
            ("make an npc dialogue", 60),
            ("farm expansion building", 75),
            ("make a TV shopping channel", 90),
        ],
    )
    async def test_estimated_seconds_is_computed_per_prompt(self, prompt, expected):
        """_estimate_seconds runs per prompt; per-item matches.

        Covers all four routing groups: texture (30), npc (60),
        farm-expansion (75), default fallthrough (90).
        """
        req = BatchGenerateRequest(user_id="u", prompts=[prompt])
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            result = await generate_mod_batch(req)
        assert result.items[0].estimated_seconds == expected

    async def test_prompts_preserved_in_item_order(self):
        """Each BatchGenerateItem echoes its prompt verbatim, in order."""
        prompts = ["first", "second", "third"]
        req = BatchGenerateRequest(user_id="u", prompts=prompts)
        create = AsyncMock()
        set_status = AsyncMock()
        bg = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("storage.queries.create_mod_request", create)
            mp.setattr("storage.redis.set_status", set_status)
            mp.setattr("orchestrator.pipeline.run_pipeline_background", bg)
            result = await generate_mod_batch(req)

        assert [item.prompt for item in result.items] == prompts
