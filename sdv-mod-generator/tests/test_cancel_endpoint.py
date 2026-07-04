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

    async def test_cancel_records_user_cancelled_reason(self, monkeypatch):
        """Should call set_cancellation_reason with "user_cancelled".

        The cancel endpoint must persist a cancellation reason
        alongside the status change so ``GET /v1/mods/{id}/cancellation_reason``
        can return it later. We verify the helper is called with the
        documented "user_cancelled" id (a member of
        ``KNOWN_CANCELLATION_REASONS``).
        """
        from app.api.routes import cancel_mod

        async def mock_get_state(rid):
            return {"status": "running"}

        async def mock_set_status(rid, status):
            return None

        reason_calls = []

        async def mock_set_cancellation_reason(rid, reason):
            reason_calls.append((rid, reason))

        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)
        monkeypatch.setattr("storage.redis.set_status", mock_set_status)
        monkeypatch.setattr(
            "storage.redis.set_cancellation_reason", mock_set_cancellation_reason
        )

        await cancel_mod("req-reason-1")
        assert reason_calls == [("req-reason-1", "user_cancelled")]

    async def test_cancel_response_includes_reason(self, monkeypatch):
        """Should surface the recorded reason in the JSON response.

        The response payload gains a ``cancellation_reason`` field
        alongside ``request_id``, ``status``, and ``previous_status``.
        Clients can use it to confirm the reason was recorded without
        making a follow-up ``GET /v1/mods/{id}/cancellation_reason`` call.
        """
        from app.api.routes import cancel_mod

        async def mock_get_state(rid):
            return {"status": "running"}

        async def mock_set_status(rid, status):
            return None

        async def mock_set_cancellation_reason(rid, reason):
            return None

        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)
        monkeypatch.setattr("storage.redis.set_status", mock_set_status)
        monkeypatch.setattr(
            "storage.redis.set_cancellation_reason", mock_set_cancellation_reason
        )

        result = await cancel_mod("req-reason-2")
        assert result["request_id"] == "req-reason-2"
        assert result["status"] == "cancelled"
        assert result["previous_status"] == "running"
        assert result["cancellation_reason"] == "user_cancelled"

    async def test_cancel_reason_write_failure_does_not_fail_request(self, monkeypatch):
        """A failure to write the reason key should not abort the cancel.

        The user's intent (stop the request) is honored either way.
        A transient Redis error on the reason write is logged at
        WARNING but the endpoint still returns 200 with
        ``cancellation_reason: None`` so the client can retry the
        reason lookup later. Note: ConnectionError is the realistic
        failure mode; RuntimeError is included as the broader
        contract surface per the source's narrow catch.
        """
        from app.api.routes import cancel_mod

        async def mock_get_state(rid):
            return {"status": "running"}

        async def mock_set_status(rid, status):
            return None

        async def mock_set_cancellation_reason(rid, reason):
            raise ConnectionError("redis transient outage")

        monkeypatch.setattr("storage.redis.get_pipeline_state", mock_get_state)
        monkeypatch.setattr("storage.redis.set_status", mock_set_status)
        monkeypatch.setattr(
            "storage.redis.set_cancellation_reason", mock_set_cancellation_reason
        )

        # No HTTPException — the cancel still succeeds.
        result = await cancel_mod("req-reason-3")
        assert result["status"] == "cancelled"
        assert result["previous_status"] == "running"
        assert result["cancellation_reason"] is None
