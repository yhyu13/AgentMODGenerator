"""Tests for ``storage/queries.py`` SQL helpers (Round v123).

Closes the orphan ``test_storage_queries.cpython-311-pytest-9.0.3.pyc``
left over from the Session 1 master port that added the read-side
helpers (``list_mod_requests``, ``count_mod_requests``) and the v105
``delete_old_mod_requests`` purge helper.

Strategy: mock ``storage.queries.get_session`` with an async context
manager that yields a session whose ``execute`` returns a configurable
result. Then assert the validation guards fire (invalid ``status``,
invalid ``sort``, ``days < 1`` → empty list), the right SQL fragment
is sent (text contains the expected clause / bind params), and the
Python-side row → dict mapping preserves the column names the route
layer expects.

Hermetic: no Postgres/Redis I/O, no ``app.config`` import at module
load (the ``_isolate_test_env`` autouse fixture clears LLM/proxy env
vars before any test module is collected).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import storage.queries as queries


async def _yield_session(session):
    yield session


def _patch_get_session(monkeypatch, execute_return):
    """Replace ``storage.queries.get_session`` with an async
    context manager that yields a session whose ``execute``
    returns ``execute_return``. Returns the session mock so
    callers can inspect bound params via ``await_args``.
    """
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_return)
    cm = asynccontextmanager(lambda: _yield_session(session))
    monkeypatch.setattr(queries, "get_session", cm)
    return session


def _row(**kwargs):
    """MagicMock exposing column names as attributes."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


# Validation guards — pure Python, no DB


class TestListAndCountValidation:
    """Pin the ``ValueError`` contract for the list helper."""

    def test_list_invalid_status_raises_value_error(self) -> None:
        """Bad status → ``ValueError`` BEFORE the SQL is sent."""
        with pytest.raises(ValueError, match="Invalid status"):
            import asyncio
            asyncio.run(queries.list_mod_requests(status="bogus"))

    def test_list_invalid_sort_raises_value_error(self) -> None:
        """Bad sort key → ``ValueError`` BEFORE the SQL is sent."""
        with pytest.raises(ValueError, match="Invalid sort"):
            import asyncio
            asyncio.run(queries.list_mod_requests(sort="nope"))


class TestDeleteOldModRequestsGuards:
    """Pin the destructive-helper guard rails."""

    def test_days_zero_returns_empty_without_db(self, monkeypatch) -> None:
        """``days < 1`` is a no-op: returns ``[]`` without
        touching the session (defensive; route layer already
        enforces ``1 <= days <= 365``).
        """
        session = _patch_get_session(monkeypatch, execute_return=None)
        import asyncio
        result = asyncio.run(queries.delete_old_mod_requests(days=0))
        assert result == []
        session.execute.assert_not_called()


# Read helpers — SQL wiring


class TestGetUserHistory:
    """Pin the SQL wiring of ``get_user_history``."""

    def test_returns_empty_list_when_no_rows(self, monkeypatch) -> None:
        """``fetchall() → []`` → ``[]`` (no exception)."""
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []
        _patch_get_session(monkeypatch, execute_return=empty_result)
        import asyncio
        rows = asyncio.run(queries.get_user_history("alice"))
        assert rows == []

    def test_maps_rows_to_dicts(self, monkeypatch) -> None:
        """``fetchall() → [row, ...]`` → list of dicts with the
        four keys the route layer (``get_history``) expects.
        """
        ts = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
        result = MagicMock()
        result.fetchall.return_value = [
            _row(request_id="r1", prompt="p1", status="done", created_at=ts),
            _row(request_id="r2", prompt="p2", status="failed", created_at=ts),
        ]
        _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        rows = asyncio.run(queries.get_user_history("alice", limit=10))
        assert len(rows) == 2
        assert rows[0]["request_id"] == "r1"
        assert rows[0]["status"] == "done"
        assert rows[1]["request_id"] == "r2"
        assert rows[0]["created_at"] == ts


class TestListModRequestsSQL:
    """Pin the WHERE-clause builder for ``list_mod_requests``."""

    def test_default_filters_pass_no_where_clause(self, monkeypatch) -> None:
        """No filters → no WHERE clause; defaults: limit=20, offset=0."""
        result = MagicMock()
        result.fetchall.return_value = []
        session = _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        asyncio.run(queries.list_mod_requests())
        assert session.execute.await_count == 1
        sent_sql = session.execute.await_args.args[0]
        sent_params = session.execute.await_args.args[1]
        assert "WHERE" not in str(sent_sql)
        assert sent_params["limit"] == 20
        assert sent_params["offset"] == 0

    def test_user_id_filter_adds_where_clause(self, monkeypatch) -> None:
        """``user_id`` filter → ``WHERE mr.user_id = :user_id``."""
        result = MagicMock()
        result.fetchall.return_value = []
        session = _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        asyncio.run(queries.list_mod_requests(user_id="alice"))
        sent_sql = session.execute.await_args.args[0]
        sent_params = session.execute.await_args.args[1]
        assert "WHERE mr.user_id = :user_id" in str(sent_sql)
        assert sent_params["user_id"] == "alice"


class TestCountModRequestsSQL:
    """Pin the WHERE-clause builder for ``count_mod_requests``."""

    def test_no_filters_sends_count_star_only(self, monkeypatch) -> None:
        """No filters → ``SELECT COUNT(*)`` with no WHERE clause."""
        result = MagicMock()
        result.fetchone.return_value = _row(cnt=42)
        session = _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        count = asyncio.run(queries.count_mod_requests())
        assert count == 42
        sent_sql = session.execute.await_args.args[0]
        sent_params = session.execute.await_args.args[1]
        assert "COUNT(*)" in str(sent_sql)
        assert "WHERE" not in str(sent_sql)
        assert sent_params == {}


class TestDeleteOldModRequestsSQL:
    """Pin the SQL wiring of the purge helper."""

    def test_returns_deleted_request_ids(self, monkeypatch) -> None:
        """``fetchall() → [row, ...]`` → list of ``request_id``."""
        result = MagicMock()
        result.fetchall.return_value = [
            _row(request_id="r1"),
            _row(request_id="r2"),
        ]
        _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        deleted = asyncio.run(queries.delete_old_mod_requests(days=30))
        assert deleted == ["r1", "r2"]

    def test_days_passed_as_integer_param(self, monkeypatch) -> None:
        """Days is bound as an int (SQL casts via ``|| ' days'::interval``)."""
        result = MagicMock()
        result.fetchall.return_value = []
        session = _patch_get_session(monkeypatch, execute_return=result)
        import asyncio
        asyncio.run(queries.delete_old_mod_requests(days=7))
        sent_params = session.execute.await_args.args[1]
        assert sent_params["days"] == 7
        assert isinstance(sent_params["days"], int)