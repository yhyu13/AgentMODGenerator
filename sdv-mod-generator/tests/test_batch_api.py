"""Tests for the batch generation API endpoint."""
import pytest
from fastapi import HTTPException


class TestBatchGenerateEndpoint:
    """Tests for POST /v1/mods/generate/batch."""

    async def test_batch_generates_multiple(self, monkeypatch):
        """Should create multiple requests and return batch info."""
        from app.api.routes import generate_mod_batch
        from app.api.schemas import BatchGenerateRequest

        started = []
        async def mock_create_mod_request(request_id, user_id, prompt, phase, files, meta):
            pass
        async def mock_set_status(rid, status):
            pass
        def mock_run_background(request_id, user_id, prompt):
            started.append(request_id)

        monkeypatch.setattr("storage.queries.create_mod_request", mock_create_mod_request)
        monkeypatch.setattr("storage.redis.set_status", mock_set_status)
        monkeypatch.setattr("orchestrator.pipeline.run_pipeline_background", mock_run_background)

        req = BatchGenerateRequest(user_id="user1", prompts=["mod a", "mod b", "mod c"])
        result = await generate_mod_batch(req)
        assert result.batch_id.startswith("batch_")
        assert len(result.items) == 3
        for item in result.items:
            assert item.request_id.startswith("req_")
            assert item.status == "running"
            assert item.prompt in req.prompts
        assert len(started) == 3

    async def test_batch_empty_prompts_rejected(self, monkeypatch):
        """Empty prompts list should be rejected by schema validation."""
        from app.api.schemas import BatchGenerateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BatchGenerateRequest(user_id="user1", prompts=[])

    async def test_batch_too_many_prompts_rejected(self, monkeypatch):
        """More than 10 prompts should be rejected by schema validation."""
        from app.api.schemas import BatchGenerateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BatchGenerateRequest(user_id="user1", prompts=[f"mod {i}" for i in range(11)])


class TestEstimateSeconds:
    """Tests for the _estimate_seconds helper."""

    def test_texture_estimate(self):
        from app.api.routes import _estimate_seconds
        assert _estimate_seconds("replace a sprite") == 30

    def test_npc_estimate(self):
        from app.api.routes import _estimate_seconds
        assert _estimate_seconds("create npc schedule") == 60

    def test_farm_expansion_estimate(self):
        from app.api.routes import _estimate_seconds
        assert _estimate_seconds("farm expansion with buildings") == 75

    def test_default_estimate(self):
        from app.api.routes import _estimate_seconds
        assert _estimate_seconds("random prompt") == 90
