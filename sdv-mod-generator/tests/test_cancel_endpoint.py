"""Tests for API cancel endpoint."""
import pytest
from fastapi import HTTPException


class TestCancelEndpoint:
    """Tests for POST /v1/mods/cancel/{request_id}."""

    async def test_cancel_running_request(self, monkeypatch):
        """Should cancel a running request."""
        from app.api.routes import cancel_mod

        calls = []
        async def mock_get_state(rid):
            return {"status": "running"}
        async def mock_set_status(rid, status):
            calls.append((rid, status))

        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)
        monkeypatch.setattr("storage.redis.set_status", mock_set_status)

        result = await cancel_mod("req-abc")
        assert result["request_id"] == "req-abc"
        assert result["status"] == "cancelled"
        assert result["previous_status"] == "running"
        assert calls == [("req-abc", "cancelled")]

    async def test_cancel_fails_when_done(self, monkeypatch):
        """Should raise 400 when request is already done."""
        from app.api.routes import cancel_mod

        async def mock_get_state(rid):
            return {"status": "done"}
        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_mod("req-done")
        assert exc_info.value.status_code == 400
        assert "done" in exc_info.value.detail

    async def test_cancel_fails_when_not_found(self, monkeypatch):
        """Should raise 404 when request does not exist."""
        from app.api.routes import cancel_mod

        async def mock_get_state(rid):
            return None
        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_mod("req-missing")
        assert exc_info.value.status_code == 404
