"""Tests for Redis storage layer."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import storage.redis as redis_module


class TestGetPipelineState:
    """Tests for get_pipeline_state JSON decode handling."""

    @pytest.fixture(autouse=True)
    def reset_client(self):
        """Reset the global Redis client before each test."""
        redis_module._client = None
        yield
        redis_module._client = None

    async def test_returns_none_on_missing_key(self, monkeypatch):
        """Should return None when key does not exist in Redis."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        redis_module._client = mock_client

        result = await redis_module.get_pipeline_state("req-123")
        assert result is None

    async def test_returns_parsed_dict_on_valid_json(self, monkeypatch):
        """Should parse and return dict on valid JSON."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value='{"status": "running", "stage": 2}')
        redis_module._client = mock_client

        result = await redis_module.get_pipeline_state("req-456")
        assert result == {"status": "running", "stage": 2}

    async def test_returns_none_on_corrupted_json(self, monkeypatch):
        """Should return None and log warning on corrupted JSON data."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="not valid json {{{")
        redis_module._client = mock_client

        result = await redis_module.get_pipeline_state("req-789")
        assert result is None

    async def test_returns_none_on_empty_string(self, monkeypatch):
        """Should return None on empty string data."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="")
        redis_module._client = mock_client

        result = await redis_module.get_pipeline_state("req-empty")
        assert result is None


class TestSetPipelineState:
    """Tests for set_pipeline_state."""

    @pytest.fixture(autouse=True)
    def reset_client(self):
        redis_module._client = None
        yield
        redis_module._client = None

    async def test_serializes_and_sets_with_ttl(self, monkeypatch):
        """Should serialize dict and set with TTL."""
        mock_client = AsyncMock()
        redis_module._client = mock_client

        state = {"status": "done", "zip_url": "s3://bucket/key"}
        await redis_module.set_pipeline_state("req-abc", state, ttl=3600)

        mock_client.set.assert_awaited_once()
        call_args = mock_client.set.call_args
        assert call_args[0][0] == "pipeline:req-abc"
        parsed = json.loads(call_args[0][1])
        assert parsed["status"] == "done"
        assert call_args[1].get("ex") == 3600
