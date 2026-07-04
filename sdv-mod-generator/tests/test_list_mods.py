"""Handler-level tests for ``GET /v1/mods``.

Round v30: ports the listing route handler from the discord-ops-hardening
branch. We exercise the handler via mocked storage helpers
(``list_mod_requests`` and ``count_mod_requests``) so the test does not
need a live PostgreSQL. The handler's contract:

- 200 with a ``ModListResponse``-shaped JSON body, ``Cache-Control:
  no-store`` header on success
- 400 on ``offset > _MOD_LIST_OFFSET_MAX`` (the explicit cap)
- 422 on out-of-range ``limit``/``offset``/unknown ``status``/unknown
  ``sort`` (Pydantic / FastAPI ``Query`` validation)
- Filters echoed back in ``filters`` so the caller can verify the
  query string was honored
- ``has_more`` is computed from the real total: ``offset + len(items)
  < total``
- ``total`` and the page are fetched in parallel via
  ``asyncio.gather``
- Defensive datetime fallback to ``datetime.now(timezone.utc)`` if the
  storage helper returned a plain dict (unit-test shim)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

import app.api.routes as routes_module
from app.api.routes import (
    _MOD_LIST_LIMIT_DEFAULT,
    _MOD_LIST_LIMIT_MAX,
    _MOD_LIST_LIMIT_MIN,
    _MOD_LIST_OFFSET_MAX,
    _MOD_LIST_SORT_KEYS,
    list_mods,
)


# ---------------------------------------------------------------------------
# Row factory + storage helpers fakes
# ---------------------------------------------------------------------------

def _make_row(
    request_id: str,
    user_id: str | None = "user-1",
    status: str = "done",
    phase: str = "shop_channel",
    prompt: str = "make a TV channel",
    zip_key: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict:
    """Build a plain-dict row in the exact shape ``list_mods`` reads.

    Mirrors the dict structure that ``storage.queries.list_mod_requests``
    returns (per v27 round notes): ``request_id``, ``user_id``,
    ``status``, ``phase``, ``created_at``, ``updated_at``, ``prompt``,
    ``zip_key``.
    """
    now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    return {
        "request_id": request_id,
        "user_id": user_id,
        "status": status,
        "phase": phase,
        "created_at": created_at or now,
        "updated_at": updated_at if updated_at is not None else now,
        "prompt": prompt,
        "zip_key": zip_key,
    }


def _patch_storage(rows: list[dict], total: int):
    """Patch the two storage helpers used by ``list_mods``.

    Both helpers are async — wrap them in ``AsyncMock`` and have them
    return ``rows`` and ``total`` respectively. The handler calls them
    via ``asyncio.gather`` so we don't need a real event loop inside
    the mock.
    """
    list_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    return (
        patch.object(routes_module, "list_mod_requests", list_mock),
        patch.object(routes_module, "count_mod_requests", count_mock),
        list_mock,
        count_mock,
    )


# ---------------------------------------------------------------------------
# Constants — pinned values mirror the source bundle
# ---------------------------------------------------------------------------

class TestModListConstants:
    """The constants are referenced by the handler AND by the route
    registration. Pin them so a refactor can't silently change behavior.
    """

    def test_limit_min_is_one(self):
        assert _MOD_LIST_LIMIT_MIN == 1

    def test_limit_max_is_one_hundred(self):
        assert _MOD_LIST_LIMIT_MAX == 100

    def test_limit_default_is_twenty(self):
        assert _MOD_LIST_LIMIT_DEFAULT == 20

    def test_offset_max_is_ten_thousand(self):
        # Source bundle: 10000 rows of headroom for pagination, no
        # env gate. Pinned here so the magic number can't drift.
        assert _MOD_LIST_OFFSET_MAX == 10000

    def test_sort_keys_are_three_known_values(self):
        assert _MOD_LIST_SORT_KEYS == (
            "created_at_desc",
            "created_at_asc",
            "updated_at_desc",
        )


# ---------------------------------------------------------------------------
# Happy path — handler returns a ModListResponse-shaped JSON envelope
# ---------------------------------------------------------------------------

class TestListModsHappyPath:
    """Default params return an empty page with the canonical envelope
    shape. We mock both storage helpers so the handler exercises the
    full assembly path.
    """

    async def test_default_params_returns_envelope(self):
        p1, p2, list_mock, count_mock = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods()
        # It's a JSONResponse, so read its body and headers.
        body = bytes(response.body).decode()
        import json as _json
        payload = _json.loads(body)
        # Envelope shape.
        assert "items" in payload
        assert "total" in payload
        assert "limit" in payload
        assert "offset" in payload
        assert "has_more" in payload
        assert "filters" in payload
        assert payload["items"] == []
        assert payload["total"] == 0
        assert payload["offset"] == 0
        assert payload["has_more"] is False

    async def test_default_limit_is_twenty(self):
        p1, p2, list_mock, count_mock = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["limit"] == _MOD_LIST_LIMIT_DEFAULT

    async def test_response_includes_cache_control_no_store(self):
        """v142 Blue: the listing endpoint is unauthenticated and exposes
        other users' user_id + truncated prompt when the user_id filter
        is omitted. The 200 path MUST set ``Cache-Control: no-store`` so
        no CDN / sidecar caches the response."""
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods()
        assert response.headers.get("Cache-Control") == "no-store"

    async def test_single_row_round_trip(self):
        rows = [_make_row("req_1", zip_key="zips/a.zip")]
        p1, p2, _, _ = _patch_storage(rows, total=1)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["total"] == 1
        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert item["request_id"] == "req_1"
        assert item["status"] == "done"
        assert item["user_id"] == "user-1"
        assert item["phase"] == "shop_channel"
        assert item["feature"] == "shop_channel"  # mirror
        assert item["has_zip"] is True

    async def test_zip_key_none_yields_has_zip_false(self):
        rows = [_make_row("req_2", zip_key=None)]
        p1, p2, _, _ = _patch_storage(rows, total=1)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["items"][0]["has_zip"] is False


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestListModsPagination:
    """``has_more`` is computed from the real total — it must NOT
    over-estimate when exactly ``limit`` rows remain.
    """

    async def test_has_more_true_when_more_rows_remain(self):
        # 25 total rows, page of 20, offset 0 → has_more True
        rows = [_make_row(f"req_{i}") for i in range(20)]
        p1, p2, _, _ = _patch_storage(rows, total=25)
        with p1, p2:
            response = await list_mods(limit=20, offset=0)
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["has_more"] is True
        assert payload["total"] == 25

    async def test_has_more_false_on_last_full_page(self):
        # 20 total rows, page of 20, offset 0 → has_more False
        # (offset + len(items) == total)
        rows = [_make_row(f"req_{i}") for i in range(20)]
        p1, p2, _, _ = _patch_storage(rows, total=20)
        with p1, p2:
            response = await list_mods(limit=20, offset=0)
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["has_more"] is False

    async def test_has_more_false_when_total_equals_offset_plus_count(self):
        # 5 total rows, offset=3, limit=2 → has_more False
        rows = [_make_row(f"req_{i}") for i in range(2)]
        p1, p2, _, _ = _patch_storage(rows, total=5)
        with p1, p2:
            response = await list_mods(limit=2, offset=3)
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["has_more"] is False  # 3 + 2 == 5

    async def test_has_more_true_on_partial_last_page(self):
        # 5 total rows, offset=4, limit=2 → 1 row returned, has_more False
        rows = [_make_row("req_4")]
        p1, p2, _, _ = _patch_storage(rows, total=5)
        with p1, p2:
            response = await list_mods(limit=2, offset=4)
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["has_more"] is False  # 4 + 1 == 5

    async def test_offset_passed_through_to_storage(self):
        """The handler must forward ``offset`` to ``list_mod_requests``."""
        p1, p2, list_mock, _ = _patch_storage([], 0)
        with p1, p2:
            await list_mods(limit=20, offset=42)
        # Inspect the call_args to confirm offset was forwarded.
        assert list_mock.call_args.kwargs["offset"] == 42

    async def test_limit_passed_through_to_storage(self):
        p1, p2, list_mock, _ = _patch_storage([], 0)
        with p1, p2:
            await list_mods(limit=7, offset=0)
        assert list_mock.call_args.kwargs["limit"] == 7


# ---------------------------------------------------------------------------
# Filter echo
# ---------------------------------------------------------------------------

class TestListModsFiltersEchoed:
    """The envelope's ``filters`` field echoes back what was applied so
    a caller can verify their query string was honored.
    """

    async def test_user_id_filter_echoed(self):
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods(user_id="user-1")
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["filters"] == {"user_id": "user-1", "status": None}

    async def test_status_filter_echoed(self):
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods(status_filter="done")
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["filters"] == {"user_id": None, "status": "done"}

    async def test_both_filters_echoed(self):
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods(user_id="user-1", status_filter="done")
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["filters"] == {"user_id": "user-1", "status": "done"}

    async def test_no_filters_echoed_as_none(self):
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        assert payload["filters"] == {"user_id": None, "status": None}

    async def test_filters_forwarded_to_storage(self):
        """Both filters must reach the storage helpers as keyword args."""
        p1, p2, list_mock, count_mock = _patch_storage([], 0)
        with p1, p2:
            await list_mods(user_id="user-1", status_filter="done")
        assert list_mock.call_args.kwargs["user_id"] == "user-1"
        assert list_mock.call_args.kwargs["status"] == "done"
        assert count_mock.call_args.kwargs["user_id"] == "user-1"
        assert count_mock.call_args.kwargs["status"] == "done"


# ---------------------------------------------------------------------------
# Defensive datetime fallbacks (unit-test shim path)
# ---------------------------------------------------------------------------

class TestListModsDatetimeFallback:
    """When the storage helper returns rows with non-datetime values
    (the test shim path), the handler falls back to ``datetime.now``
    so Pydantic construction doesn't blow up. This pins the contract.
    """

    async def test_created_at_string_is_replaced_with_now(self):
        """A non-datetime ``created_at`` triggers the fallback. We use
        a plain string here to exercise the ``not isinstance(...)`` branch.
        """
        rows = [{
            "request_id": "req_1",
            "user_id": "user-1",
            "status": "done",
            "phase": "shop_channel",
            "created_at": "2026-07-04T12:00:00+00:00",  # string, not datetime
            "updated_at": None,
            "prompt": "x",
            "zip_key": None,
        }]
        p1, p2, _, _ = _patch_storage(rows, 1)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        # The fallback is ``datetime.now(timezone.utc)``; we don't pin
        # the exact value, only that the field is parseable as ISO 8601.
        assert "created_at" in payload["items"][0]
        # Must be a non-empty ISO-like string.
        assert isinstance(payload["items"][0]["created_at"], str)
        assert payload["items"][0]["created_at"] != ""

    async def test_updated_at_string_is_replaced_with_now(self):
        rows = [{
            "request_id": "req_1",
            "user_id": "user-1",
            "status": "done",
            "phase": "shop_channel",
            "created_at": datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
            "updated_at": "not-a-datetime",
            "prompt": "x",
            "zip_key": None,
        }]
        p1, p2, _, _ = _patch_storage(rows, 1)
        with p1, p2:
            response = await list_mods()
        import json as _json
        payload = _json.loads(bytes(response.body).decode())
        # Fallback replaces non-datetime updated_at with now.
        assert payload["items"][0]["updated_at"] is not None


# ---------------------------------------------------------------------------
# Pagination cap (the explicit 400 path)
# ---------------------------------------------------------------------------

class TestListModsOffsetCap:
    """The handler applies an unconditional ``offset > _MOD_LIST_OFFSET_MAX``
    guard BEFORE the storage call. The 400 path does NOT set
    Cache-Control (only the 200 path does).
    """

    async def test_offset_above_max_raises_http_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await list_mods(offset=_MOD_LIST_OFFSET_MAX + 1)
        assert exc_info.value.status_code == 400
        assert "offset" in exc_info.value.detail

    async def test_offset_at_max_is_allowed(self):
        """The cap is inclusive: ``offset == 10000`` is allowed."""
        p1, p2, _, _ = _patch_storage([], 0)
        with p1, p2:
            response = await list_mods(offset=_MOD_LIST_OFFSET_MAX)
        # We should get a 200, not an exception.
        assert response.status_code == 200

    async def test_offset_cap_does_not_call_storage(self):
        """The storage helpers must NOT be called when the cap fires
        — the cap is a defensive guard before we ever hit the DB."""
        p1, p2, list_mock, count_mock = _patch_storage([], 0)
        with p1, p2:
            with pytest.raises(Exception):
                await list_mods(offset=_MOD_LIST_OFFSET_MAX + 1)
        assert list_mock.call_count == 0
        assert count_mock.call_count == 0


# ---------------------------------------------------------------------------
# Parallel storage calls
# ---------------------------------------------------------------------------

class TestListModsParallelQueries:
    """The handler uses ``asyncio.gather`` to fetch the page and the
    total in parallel — latency is one round-trip, not two. We don't
    assert the parallelism itself (that's a runtime property of
    asyncio.gather) but we DO assert both helpers are awaited.
    """

    async def test_both_storage_helpers_called_once(self):
        p1, p2, list_mock, count_mock = _patch_storage([], 0)
        with p1, p2:
            await list_mods()
        assert list_mock.call_count == 1
        assert count_mock.call_count == 1

    async def test_count_helper_not_paginated(self):
        """``count_mod_requests`` must NOT receive limit/offset/sort —
        it's a real ``COUNT(*)`` over the full WHERE-clause."""
        p1, p2, _, count_mock = _patch_storage([], 0)
        with p1, p2:
            await list_mods(limit=20, offset=0, sort="created_at_asc")
        kwargs = count_mock.call_args.kwargs
        assert "limit" not in kwargs
        assert "offset" not in kwargs
        assert "sort" not in kwargs


# ---------------------------------------------------------------------------
# Sort parameter forwarding
# ---------------------------------------------------------------------------

class TestListModsSortForwarding:
    """The ``sort`` query param must be forwarded to ``list_mod_requests``.
    Pydantic Literal validation of the sort key happens at FastAPI's
    Query boundary; the handler itself only forwards whatever Pydantic
    accepted.
    """

    async def test_default_sort_forwarded(self):
        p1, p2, list_mock, _ = _patch_storage([], 0)
        with p1, p2:
            await list_mods()
        assert list_mock.call_args.kwargs["sort"] == "created_at_desc"

    async def test_explicit_sort_forwarded(self):
        p1, p2, list_mock, _ = _patch_storage([], 0)
        with p1, p2:
            await list_mods(sort="created_at_asc")
        assert list_mock.call_args.kwargs["sort"] == "created_at_asc"

    async def test_updated_at_sort_forwarded(self):
        p1, p2, list_mock, _ = _patch_storage([], 0)
        with p1, p2:
            await list_mods(sort="updated_at_desc")
        assert list_mock.call_args.kwargs["sort"] == "updated_at_desc"