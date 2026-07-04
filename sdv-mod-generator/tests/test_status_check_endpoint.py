"""Tests for ``GET /v1/mods/status/{request_id}`` (status-check endpoint).

Round v69: tests for the ``get_mod_status_check`` route handler at
``app/api/routes.py:184-194`` — the simple Redis-cached status read
that backs the lightweight ``/v1/mods/status/{request_id}`` endpoint
(distinct from ``/v1/mods/{request_id}`` which is the full
``ModStatusResponse`` read from ``app/api/routes.py:2120``).

A stale ``__pycache__/test_status_check_endpoint.cpython-311-pytest-9.0.3.pyc``
artifact exists on master but the live ``.py`` source is missing —
same pattern that v68 fixed for ``test_cancellation_reason_endpoint.py``.
v69 restores that coverage.

The handler has two execution paths:

1. **Redis hit** — ``storage.redis.get_status(request_id)`` returns a
   non-None status string. The handler returns the dict
   ``{"request_id": request_id, "status": current_status}`` with
   HTTP 200 (no explicit response_model, so the dict is the body).

2. **Redis miss** — ``get_status`` returns ``None``. The handler
   raises ``HTTPException(404, ...)`` BEFORE doing anything else.
   No DB fallback, no async resource is touched beyond the single
   ``get_status`` call. The 404 is the documented contract for an
   unknown request_id at this endpoint.

The handler is intentionally lightweight — no auth, no API key
check, no DB fallback — so its test surface is correspondingly
small. Mirrors the ``tests/test_cancellation_reason_endpoint.py``
convention (v68): direct async handler invocation with
``monkeypatch.setattr`` on ``storage.redis.get_status``.

Covers:
- ``get_mod_status_check``:
  - happy path: Redis returns "running" → dict echoes request_id + status
  - Redis returns "done" (terminal status string passthrough)
  - Redis returns "failed" (non-cancelled terminal — passthrough)
  - Redis returns "pending" (initial pipeline state — passthrough)
  - Redis returns "cancelled" (passthrough; the singular
    ``/v1/mods/{id}/cancellation_reason`` carries the rich payload)
  - Redis returns "unknown" (defensive: an unexpected string passes
    through unchanged so the endpoint is a stable Redis read surface,
    not a normalizer)
  - Redis miss → HTTPException 404 with the documented message
  - request_id echo: the dict's request_id is the path parameter,
    not whatever was in Redis (Redis is the status only)
  - handler does NOT call any DB helper on the Redis-miss path
    (verified by giving ``get_mod_output`` a raising mock)
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


class TestStatusCheckEndpointRedisHit:
    """Tests for the happy path: Redis has a status for the request_id."""

    async def test_returns_running_status(self, monkeypatch):
        """Pipeline mid-flight: status="running" echoes through."""
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            assert rid == "req-running-1"
            return "running"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-running-1")
        assert result == {"request_id": "req-running-1", "status": "running"}

    async def test_returns_done_status(self, monkeypatch):
        """Terminal success: status="done" echoes through unchanged.

        The handler does no validation on the status string — it is
        a raw passthrough from Redis. This pins the contract so a
        future refactor that adds a status whitelist surfaces here.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return "done"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-done-1")
        assert result == {"request_id": "req-done-1", "status": "done"}

    async def test_returns_failed_status(self, monkeypatch):
        """Terminal failure: status="failed" echoes through."""
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return "failed"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-failed-1")
        assert result == {"request_id": "req-failed-1", "status": "failed"}

    async def test_returns_pending_status(self, monkeypatch):
        """Initial pipeline state: status="pending" echoes through."""
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return "pending"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-pending-1")
        assert result == {"request_id": "req-pending-1", "status": "pending"}

    async def test_returns_cancelled_status(self, monkeypatch):
        """Cancelled request: status="cancelled" echoes through.

        The richer cancellation payload (the reason) is on the
        dedicated ``/v1/mods/{id}/cancellation_reason`` endpoint
        (covered by v68's ``test_cancellation_reason_endpoint.py``).
        This endpoint is the lightweight status read.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return "cancelled"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-cancel-1")
        assert result == {"request_id": "req-cancel-1", "status": "cancelled"}

    async def test_returns_unknown_status_unchanged(self, monkeypatch):
        """Defensive passthrough: an unrecognized status string is
        returned unchanged.

        The handler does not normalize or validate the status — it
        is a thin Redis read surface. If Redis carries a transient
        or experimental status (e.g. "queued", "rate_limited"),
        the endpoint surfaces it as-is so the client sees the same
        state the pipeline wrote. A future normalizer should land
        here, not be silently applied.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return "queued_for_review"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-q-1")
        assert result == {
            "request_id": "req-q-1",
            "status": "queued_for_review",
        }


class TestStatusCheckEndpointRedisMiss:
    """Tests for the Redis-miss path: handler raises 404."""

    async def test_raises_404_when_status_missing(self, monkeypatch):
        """Unknown request_id → HTTPException 404.

        The handler checks the Redis status first and raises 404
        BEFORE doing anything else. There is no DB fallback at
        this endpoint — the dedicated ``/v1/mods/{request_id}``
        endpoint (line 2120) is the full read with DB fallback.
        This endpoint is the lightweight Redis-only check.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return None  # Redis miss

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        with pytest.raises(HTTPException) as exc_info:
            await get_mod_status_check("req-unknown-1")

        assert exc_info.value.status_code == 404
        assert "req-unknown-1" in str(exc_info.value.detail)
        assert "Status not found" in str(exc_info.value.detail)

    async def test_does_not_call_db_on_redis_miss(self, monkeypatch):
        """The handler does NOT touch the DB on a Redis miss.

        On the Redis-miss path the handler must raise 404 before
        any other I/O. We verify this by giving ``get_mod_output``
        a raising mock — if the handler ever falls through to the
        DB on the miss path, the test fails on the un-awaited
        RuntimeError, not on a silent bug.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            return None

        async def mock_get_mod_output(rid):
            raise AssertionError(
                "get_mod_output must NOT be called on Redis miss"
            )

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)
        monkeypatch.setattr(
            "storage.queries.get_mod_output", mock_get_mod_output
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_mod_status_check("req-no-fallback-1")

        assert exc_info.value.status_code == 404


class TestStatusCheckEndpointRequestIdEcho:
    """Tests that the path parameter request_id is echoed in the response."""

    async def test_request_id_is_path_parameter_not_redis_payload(
        self, monkeypatch
    ):
        """The dict's request_id must come from the path parameter.

        Redis stores the status (a string), not a request_id —
        the handler binds the request_id from the URL, not from
        the Redis value. We pin this by returning a status that
        does NOT look like a request_id and asserting the dict's
        request_id is still the path parameter.
        """
        from app.api.routes import get_mod_status_check

        async def mock_get_status(rid):
            assert rid == "req-echo-1"
            return "done"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        result = await get_mod_status_check("req-echo-1")
        assert result["request_id"] == "req-echo-1"
        assert result["status"] == "done"

    async def test_redis_callback_receives_path_parameter(self, monkeypatch):
        """The mock is invoked with the path parameter so a caller
        can assert cache key isolation in a real Redis deployment.

        This pins that the handler binds ``request_id`` to the
        path parameter and forwards it to ``storage.redis.get_status``
        unmodified — a future refactor that strips, lowercases,
        or hashes the key would surface here.
        """
        from app.api.routes import get_mod_status_check

        seen = []

        async def mock_get_status(rid):
            seen.append(rid)
            return "running"

        monkeypatch.setattr("storage.redis.get_status", mock_get_status)

        await get_mod_status_check("req-with-DASH-and-UNDERSCORE_99")
        assert seen == ["req-with-DASH-and-UNDERSCORE_99"]