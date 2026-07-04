"""Tests for the GET /v1/mods/{request_id}/cancellation_reason endpoint.

The handler is the per-request read-side companion to POST /v1/mods/cancel/
{request_id} (see tests/test_cancel_endpoint.py). It returns the stored
reason for a cancelled request — useful for chat bots / dashboards that
want to surface "why was my request cancelled?" without paying for the
full ModStatusResponse payload.

Test strategy (mirrors test_cancel_endpoint.py + test_metadata_endpoint.py):

- Direct async invocation of the handler ``get_cancellation_reason_endpoint``
  rather than TestClient — the handler is pure async with two mocked Redis
  reads, no dependency injection to wrestle with.
- ``monkeypatch.setattr`` on ``storage.redis.get_status`` and
  ``storage.redis.get_cancellation_reason`` (these are the exact module
  paths imported inside the handler, per routes.py:524).
- Pin every documented branch:
    1. happy path — cancelled + known reason → 200, surfaces the reason
    2. happy path — cancelled + null reason (pre-feature cancellation)
    3. not found — get_status returns None → 404
    4. not cancelled — get_status returns "running"/"done"/"failed" → 400
    5. transient Redis failure on the reason lookup → 200 with null
       (matches the handler's narrow catch: ConnectionError,
       asyncio.TimeoutError, RuntimeError)
    6. response shape — schema fields match expected types / literals
"""
import asyncio

import pytest
from fastapi import HTTPException


class TestGetCancellationReasonEndpoint:
    """Tests for GET /v1/mods/{request_id}/cancellation_reason."""

    async def test_returns_stored_reason_for_cancelled_request(self, monkeypatch):
        """Happy path: status=cancelled + a recorded reason.

        Verifies the handler returns the documented
        CancellationReasonResponse(request_id, status="cancelled",
        cancellation_reason=<stored>). The literal "cancelled" status is
        enforced by the schema (Literal["cancelled"]) so the response
        shape is constrained at validation time.
        """
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            assert rid == "req-cancel-1"
            return "cancelled"

        async def mock_get_cancellation_reason(rid):
            assert rid == "req-cancel-1"
            return "user_cancelled"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        result = await get_cancellation_reason_endpoint("req-cancel-1")
        assert result.request_id == "req-cancel-1"
        assert result.status == "cancelled"
        assert result.cancellation_reason == "user_cancelled"

    async def test_returns_null_reason_for_legacy_cancellation(self, monkeypatch):
        """Pre-feature cancellations: status=cancelled but reason missing.

        The reason field was added after the status field. Some Redis
        entries will have status="cancelled" without a corresponding
        reason key. The endpoint must surface null in that case (NOT
        raise) — see the docstring at routes.py:520-522. This is the
        "null field is reserved for cancellations that pre-date the
        reason-key feature" case.
        """
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return "cancelled"

        async def mock_get_cancellation_reason(rid):
            return None  # reason key never written

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        result = await get_cancellation_reason_endpoint("req-legacy")
        assert result.request_id == "req-legacy"
        assert result.status == "cancelled"
        assert result.cancellation_reason is None

    async def test_returns_404_when_status_missing(self, monkeypatch):
        """Unknown request_id → 404.

        The handler checks get_status first; if the request is unknown
        (Redis miss), it raises 404 BEFORE attempting the reason lookup.
        We verify get_cancellation_reason is NOT called by giving it a
        raising mock — if the handler ever falls through, the test fails
        on the un-awaited RuntimeError, not on a silent bug.
        """
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return None

        async def mock_get_cancellation_reason(rid):
            raise AssertionError(
                "get_cancellation_reason should NOT be called when status is missing"
            )

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_cancellation_reason_endpoint("req-ghost")
        assert exc_info.value.status_code == 404
        assert "req-ghost" in exc_info.value.detail

    @pytest.mark.parametrize(
        "non_cancelled_status",
        ["running", "done", "failed", "pending", "error", "unknown"],
    )
    async def test_returns_400_for_non_cancelled_status(
        self, monkeypatch, non_cancelled_status
    ):
        """Status must be exactly 'cancelled' — every other value → 400.

        The docstring at routes.py:518-522 says "cancellation reason is
        meaningless for non-cancelled requests". Parametrize every
        non-cancelled status to pin the contract. Includes 'unknown'
        (the default fallback in get_pipeline_state callers — see
        routes.py:428 for the precedent) and 'error' (a status the
        pipeline may write on unhandled exceptions).
        """
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return non_cancelled_status

        async def mock_get_cancellation_reason(rid):
            raise AssertionError(
                "get_cancellation_reason should NOT be called when status is "
                "non-cancelled"
            )

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_cancellation_reason_endpoint("req-non-cancelled")
        assert exc_info.value.status_code == 400
        # The error message echoes the current status so clients can
        # tell "running" vs "done" vs "failed" without a follow-up call.
        assert non_cancelled_status in exc_info.value.detail

    async def test_transient_redis_failure_returns_null_reason(self, monkeypatch):
        """Transient Redis error on reason lookup → 200 with null.

        The handler's narrow catch (ConnectionError, asyncio.TimeoutError,
        RuntimeError — see routes.py:547) logs a WARNING and surfaces
        cancellation_reason=None rather than failing the request. This
        is the documented graceful-degradation contract: the status
        lookup succeeded, the caller knows the request is cancelled;
        the reason can be back-filled by a follow-up call once Redis
        recovers.
        """
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return "cancelled"

        async def mock_get_cancellation_reason(rid):
            raise ConnectionError("redis transient outage")

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        result = await get_cancellation_reason_endpoint("req-transient")
        assert result.request_id == "req-transient"
        assert result.status == "cancelled"
        assert result.cancellation_reason is None

    async def test_asyncio_timeout_on_reason_lookup_returns_null(
        self, monkeypatch
    ):
        """asyncio.TimeoutError → 200 with null (covered by narrow catch)."""
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return "cancelled"

        async def mock_get_cancellation_reason(rid):
            raise asyncio.TimeoutError("redis read timed out")

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        result = await get_cancellation_reason_endpoint("req-timeout")
        assert result.cancellation_reason is None
        assert result.status == "cancelled"

    async def test_runtime_error_on_reason_lookup_returns_null(self, monkeypatch):
        """RuntimeError → 200 with null (the broader contract surface)."""
        from app.api.routes import get_cancellation_reason_endpoint

        async def mock_get_status(rid):
            return "cancelled"

        async def mock_get_cancellation_reason(rid):
            raise RuntimeError("redis pool exhausted")

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.redis.get_cancellation_reason", mock_get_cancellation_reason
        )

        result = await get_cancellation_reason_endpoint("req-runtime")
        assert result.cancellation_reason is None


class TestCancellationReasonResponseSchema:
    """Schema-level tests for CancellationReasonResponse (parallel to the
    schema tests in test_cancellation_reasons.py for the list endpoint)."""

    def test_basic_construction(self):
        from app.api.schemas import CancellationReasonResponse

        resp = CancellationReasonResponse(
            request_id="req-x",
            status="cancelled",
            cancellation_reason="user_cancelled",
        )
        assert resp.request_id == "req-x"
        assert resp.status == "cancelled"
        assert resp.cancellation_reason == "user_cancelled"

    def test_status_literal_rejects_non_cancelled(self):
        """The ``status`` field is a Literal['cancelled'] — other strings
        must fail at construction time so a wrong-status bug surfaces
        at the schema layer, not at the HTTP layer.
        """
        from pydantic import ValidationError

        from app.api.schemas import CancellationReasonResponse

        with pytest.raises(ValidationError):
            CancellationReasonResponse(
                request_id="req-x",
                status="running",  # type: ignore[arg-type]
                cancellation_reason=None,
            )

    def test_null_reason_allowed(self):
        """cancellation_reason is Optional[str] — null is the documented
        value for pre-feature cancellations (see handler docstring)."""
        from app.api.schemas import CancellationReasonResponse

        resp = CancellationReasonResponse(
            request_id="req-x",
            status="cancelled",
            cancellation_reason=None,
        )
        assert resp.cancellation_reason is None