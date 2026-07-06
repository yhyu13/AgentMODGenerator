"""HTTP-layer 200/400/422 contract tests for ``GET /v1/mods``.

Closes the **second of eight** Session 1 introspection endpoints at
the TestClient layer. The schedule (v162 cron update):

  - **list_mods       — TestClient (this file) — second TestClient
                        coverage for a Session 1 endpoint**
  - get_mod_stats     — TestClient (v163 ``test_mod_stats_endpoint.py``)
  - cancellation_reasons — handler-direct only (next pick: v165)
  - list_generators / list_phases / list_known_phases /
    get_phase_detail — handler-direct only
  - (Session 3 sub-resources + Session 4 packs/route_preview all
    still handler-direct only — future cron picks)

The list endpoint is unique among the Session 1 endpoints because
the wire shape is **page-shaped**, not single-row-shaped: the
envelope carries ``items`` + ``total`` + ``limit`` + ``offset`` +
``has_more`` + ``filters``, with the handler's ``asyncio.gather``
issuing the page + count in parallel. The v30 handler-direct
test in ``test_list_mods.py`` already covers the
*contract-within-the-handler* (envelope shape, filter echo,
pagination math, parallel storage calls, defensive datetime
fallback). This file covers the **wire surface that only the
TestClient layer can see**:

  1. **Happy path 200** — multi-row response, full
     ``ModListResponse`` shape on the wire, the v142 Blue
     ``Cache-Control: no-store`` header is present, and
     ``Content-Type: application/json`` (not ``text/html``).
  2. **Empty page 200** — ``items=[]``, ``total=0``,
     ``has_more=False``, ``Cache-Control: no-store`` is still
     set. The endpoint is NOT 404 for an empty registry.
  3. **Filter echo 200** — ``user_id`` + ``status`` filters
     round-trip in the ``filters`` field on the wire.
  4. **Sort key forwarding 200** — three valid sort keys are
     forwarded (``created_at_desc`` default, ``created_at_asc``,
     ``updated_at_desc``). 422 on a sort key outside the
     Pydantic Literal.
  5. **422 wire shape** — invalid ``status`` (e.g. ``finished``),
     invalid ``sort`` (e.g. ``updated_at_asc``), ``limit=0`` (out
     of ``ge=1`` range), ``limit=101`` (out of ``le=100`` range),
     ``offset=-1`` (out of ``ge=0`` range) all return ``422`` with
     a FastAPI-shaped error envelope (``detail`` is a list of
     Pydantic error dicts). The handler is NOT called for any of
     these — FastAPI's ``Query`` validator short-circuits at the
     route boundary.
  6. **400 cap wire shape** — ``offset=10001`` returns ``400``
     with the handler's ``{"detail": "offset must be <= 10000"}``
     envelope and ``Cache-Control: no-store`` is NOT set (only
     the 200 path gets the header). The storage helpers are
     NOT called.

Why this matters in production: dashboards paginating through
the endpoint key off the ``has_more`` flag, the ``Content-Type``
header (for ``fetch().json()``), and the ``Cache-Control`` header
(for correctness behind a CDN). A regression that dropped any of
those headers would not surface in the v30 handler-direct
test — the TestClient seam is the only place where the
**wire-level headers and Pydantic 422 envelope** can be
pinned.

Both ``list_mod_requests`` and ``count_mod_requests`` are async
functions (their definitions are in ``storage/queries.py``),
imported at module-top-level in ``app/api/routes.py`` lines
74-75. The handler awaits both inside an ``asyncio.gather``
(line 3462), so the correct mock type is ``AsyncMock`` — NOT
``MagicMock``. Same mock-type pattern as v163
``test_mod_stats_endpoint.py`` (its single storage helper is
also awaited) and the opposite of the v152-v160 feature-flag
TestClient files (which patch sync helpers with ``MagicMock``).

The handler's defensive ``if limit < _MOD_LIST_LIMIT_MIN``
clamp (line 3440-3441) is interesting from a TestClient
perspective: it means ``limit=0`` returns 422 (Pydantic
``ge=1`` wins) before the handler ever runs, not 200. The
422 test pins that.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Row factory — mirrors the v30 test's _make_row helper, simplified for the
# TestClient seam (we only need a wire shape that the route can serialize).
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
    ``zip_key``. The handler defensively normalizes non-datetime values
    via the ``isinstance(...)`` guard at routes.py:3483-3488, so this
    factory hands real ``datetime`` objects to keep the wire path
    identical to production.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """TestClient against the running FastAPI app.

    The listing endpoint is unauthenticated by design (per the
    route docstring: "no auth — the listing exposes only
    metadata"), so no auth header is required. The TestClient
    spins up the real ``app`` object, which exercises the
    full route-registration chain (prefix ``/v1``, the
    ``@router.get("/mods", ...)`` decorator, and the
    ``response_model=ModListResponse`` model serializer).
    """
    return TestClient(app)


# ---------------------------------------------------------------------------
# Happy path — 200 with multi-row envelope
# ---------------------------------------------------------------------------

class TestListModsEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/mods``."""

    def test_multi_row_returns_200_with_full_envelope(
        self, client: TestClient,
    ) -> None:
        """Three rows across two users + two phases, ``total=3``,
        ``has_more=False`` (3 returned, 0 remaining), full
        ``ModListResponse`` wire shape, ``Content-Type:
        application/json``, ``Cache-Control: no-store`` is set.

        This is the load-bearing happy-path: a dashboard hitting
        ``GET /v1/mods?limit=20`` should get a JSON envelope
        with the page + total + filters all populated, plus
        the security-relevant ``no-store`` header. A regression
        that dropped ``Cache-Control`` here would silently
        re-introduce the v142 Blue CDN-cache leak.
        """
        rows = [
            _make_row("req_1", user_id="user-1", phase="shop_channel"),
            _make_row("req_2", user_id="user-2", phase="weather_event"),
            _make_row("req_3", user_id="user-1", phase="shop_channel",
                      zip_key="zips/req_3.zip"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=rows)
            count_mock = AsyncMock(return_value=3)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # Content-Type — dashboards key off this for fetch().json().
        assert r.headers["content-type"].startswith("application/json")
        # Cache-Control — v142 Blue. MUST be ``no-store`` for
        # the 200 path so no CDN / sidecar caches the listing
        # of other users' user_ids.
        assert r.headers["cache-control"] == "no-store"

        body = r.json()
        # Top-level fields all present.
        assert set(body.keys()) == {
            "items", "total", "limit", "offset", "has_more", "filters",
        }
        assert body["total"] == 3
        assert body["limit"] == 20  # _MOD_LIST_LIMIT_DEFAULT
        assert body["offset"] == 0
        assert body["has_more"] is False
        assert body["filters"] == {"user_id": None, "status": None}
        assert len(body["items"]) == 3

        # Items preserve the row order returned by the storage
        # helper (the handler does NOT re-sort; the helper
        # applies the ``sort`` query via SQL ``ORDER BY``).
        request_ids = [item["request_id"] for item in body["items"]]
        assert request_ids == ["req_1", "req_2", "req_3"]

        # The third row has a zip_key; ``has_zip`` should be True.
        assert body["items"][2]["has_zip"] is True
        # The first two have no zip_key; ``has_zip`` should be False.
        assert body["items"][0]["has_zip"] is False
        assert body["items"][1]["has_zip"] is False

        # Both storage helpers were called exactly once.
        assert list_mock.await_count == 1
        assert count_mock.await_count == 1

    def test_empty_page_returns_200_not_404(
        self, client: TestClient,
    ) -> None:
        """Empty ``mod_requests`` table — ``items=[]``, ``total=0``,
        ``has_more=False``. The endpoint is NOT 404 for an
        empty registry; same defensive-empty pattern that
        ``/v1/feature_flags`` and ``/v1/packs`` use.

        ``Cache-Control: no-store`` is still set even on the
        empty page; the 200 path always sets the header.
        """
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods")

        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 20
        assert body["offset"] == 0
        assert body["has_more"] is False
        assert body["filters"] == {"user_id": None, "status": None}

    def test_filters_echoed_in_envelope(self, client: TestClient) -> None:
        """``user_id`` + ``status`` filters round-trip in the
        ``filters`` field on the wire. The handler's envelope
        includes the actual filter values applied (after Pydantic
        validation) so a caller can verify their query string
        was honored even when defaults kicked in.

        Note the public name is ``status`` (per the route's
        ``alias="status"`` on the ``status_filter`` parameter)
        — the Python parameter is ``status_filter`` to avoid a
        name collision with the imported FastAPI ``status``
        module.
        """
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get(
                "/v1/mods",
                params={"user_id": "user-42", "status": "running"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["filters"] == {"user_id": "user-42", "status": "running"}
        # The forwarded call_args on the storage helper must
        # also have the filter values (the handler does NOT
        # remap ``status`` to ``status_filter`` before calling
        # the storage helper — it uses the public name).
        list_kwargs = list_mock.call_args.kwargs
        assert list_kwargs["user_id"] == "user-42"
        assert list_kwargs["status"] == "running"

    def test_has_more_true_on_partial_page(self, client: TestClient) -> None:
        """When ``total > offset + len(items)`` (i.e. another
        page exists), ``has_more`` is ``True`` on the wire.

        This is the "is there another page?" flag dashboards
        use to render a "Next >" button. Pin that the wire
        value matches the handler's ``(offset + len(items)) < total``
        computation — a refactor that switched the comparison
        to ``<=`` would over-report and break pagination.
        """
        rows = [_make_row(f"req_{i}") for i in range(20)]
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=rows)
            count_mock = AsyncMock(return_value=25)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"limit": 20, "offset": 0})

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 25
        assert len(body["items"]) == 20
        assert body["has_more"] is True  # 0 + 20 < 25

    def test_sort_keys_forwarded_to_storage(self, client: TestClient) -> None:
        """All three valid sort keys (``created_at_desc`` default,
        ``created_at_asc``, ``updated_at_desc``) are forwarded
        to the storage helper as ``sort=...`` on the wire.

        We don't pin the resulting page order (the SQL helper
        applies ``ORDER BY``); we pin that the sort key is
        forwarded unchanged so a refactor that mapped
        ``created_at_asc`` to a different key would surface
        here.
        """
        for sort_key in (
            "created_at_desc",
            "created_at_asc",
            "updated_at_desc",
        ):
            with pytest.MonkeyPatch.context() as mp:
                list_mock = AsyncMock(return_value=[])
                count_mock = AsyncMock(return_value=0)
                mp.setattr(
                    "app.api.routes.list_mod_requests", list_mock,
                )
                mp.setattr(
                    "app.api.routes.count_mod_requests", count_mock,
                )
                r = client.get("/v1/mods", params={"sort": sort_key})
            assert r.status_code == 200, (
                f"sort={sort_key!r} expected 200, got "
                f"{r.status_code}: {r.text!r}"
            )
            list_kwargs = list_mock.call_args.kwargs
            assert list_kwargs["sort"] == sort_key


# ---------------------------------------------------------------------------
# 422 wire shape — Pydantic / FastAPI Query validation short-circuits
# ---------------------------------------------------------------------------

class TestListModsEndpoint422:
    """422 contract tests for ``GET /v1/mods``.

    FastAPI's ``Query`` validators run BEFORE the handler is
    invoked. The 422 path:

      - Returns the FastAPI error envelope
        ``{"detail": [...Pydantic error dicts...]}``
      - Does NOT call the storage helpers
      - Does NOT set ``Cache-Control: no-store`` (only the 200
        path sets the header)
      - Has ``Content-Type: application/json`` (FastAPI's
        default JSON error envelope)

    Five cases pinned: invalid ``status`` literal, invalid
    ``sort`` literal, ``limit=0`` (ge=1), ``limit=101``
    (le=100), ``offset=-1`` (ge=0).
    """

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        """``status=finished`` is not in the Pydantic Literal
        (``pending`` / ``running`` / ``done`` / ``failed`` /
        ``cancelled``) so FastAPI rejects the request at the
        Query validator boundary.

        Storage helpers must NOT be called.
        """
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"status": "finished"})

        assert r.status_code == 422
        body = r.json()
        # FastAPI 422 envelope: ``detail`` is a list of error dicts.
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert len(body["detail"]) >= 1
        # The error mentions ``status`` somewhere in the
        # error dict's ``loc`` field. Loc is a list of
        # path components, e.g. ``["query", "status"]``.
        locs = [tuple(err.get("loc", [])) for err in body["detail"]]
        assert any("status" in loc for loc in locs)
        # The storage helpers were never awaited.
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0

    def test_invalid_sort_returns_422(self, client: TestClient) -> None:
        """``sort=updated_at_asc`` is not in the Pydantic
        Literal (``created_at_desc`` / ``created_at_asc`` /
        ``updated_at_desc``) so FastAPI rejects the request."""
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"sort": "updated_at_asc"})

        assert r.status_code == 422
        body = r.json()
        locs = [tuple(err.get("loc", [])) for err in body["detail"]]
        assert any("sort" in loc for loc in locs)
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0

    def test_limit_zero_below_ge_returns_422(
        self, client: TestClient,
    ) -> None:
        """``limit=0`` violates the ``ge=1`` constraint on the
        ``Query(...)`` validator. FastAPI rejects before the
        handler runs."""
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"limit": 0})

        assert r.status_code == 422
        body = r.json()
        locs = [tuple(err.get("loc", [])) for err in body["detail"]]
        assert any("limit" in loc for loc in locs)
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0

    def test_limit_above_max_returns_422(self, client: TestClient) -> None:
        """``limit=101`` violates the ``le=100`` constraint.
        FastAPI rejects."""
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"limit": 101})

        assert r.status_code == 422
        body = r.json()
        locs = [tuple(err.get("loc", [])) for err in body["detail"]]
        assert any("limit" in loc for loc in locs)
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0

    def test_negative_offset_returns_422(self, client: TestClient) -> None:
        """``offset=-1`` violates the ``ge=0`` constraint."""
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"offset": -1})

        assert r.status_code == 422
        body = r.json()
        locs = [tuple(err.get("loc", [])) for err in body["detail"]]
        assert any("offset" in loc for loc in locs)
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0


# ---------------------------------------------------------------------------
# 400 cap wire shape — handler's explicit offset cap
# ---------------------------------------------------------------------------

class TestListModsEndpoint400OffsetCap:
    """400 contract tests for the ``offset > _MOD_LIST_OFFSET_MAX``
    guard.

    The handler applies an unconditional offset cap (line 3452)
    AFTER the Pydantic ``ge=0`` validation (so the cap range
    is ``[_MOD_LIST_OFFSET_MAX+1, +inf)``). The 400 path:

      - Returns ``{"detail": "offset must be <= 10000"}``
      - Does NOT call the storage helpers (the cap is
        a defensive guard before the DB round-trip)
      - Does NOT set ``Cache-Control: no-store`` (only the
        200 path sets the header)

    Note: ``offset > 10000`` is rejected by the HANDLER (400),
    while ``offset < 0`` is rejected by Pydantic (422). The
    v82 F3 cap is a defensive ceiling; the 422 path is the
    Pydantic floor.
    """

    def test_offset_above_cap_returns_400(self, client: TestClient) -> None:
        """``offset=10001`` triggers the explicit 400 cap. The
        response detail mentions ``offset`` and the cap value
        (``10000``)."""
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"offset": 10001})

        assert r.status_code == 400
        body = r.json()
        # The handler's HTTPException carries the cap value
        # in the detail string.
        assert "offset" in body["detail"].lower()
        assert "10000" in body["detail"]
        # Storage helpers must NOT have been called.
        assert list_mock.await_count == 0
        assert count_mock.await_count == 0

    def test_offset_at_cap_is_allowed(self, client: TestClient) -> None:
        """The cap is inclusive: ``offset == 10000`` returns
        200 (not 400). The handler's ``if offset >
        _MOD_LIST_OFFSET_MAX`` test is strictly greater than.
        """
        with pytest.MonkeyPatch.context() as mp:
            list_mock = AsyncMock(return_value=[])
            count_mock = AsyncMock(return_value=0)
            mp.setattr("app.api.routes.list_mod_requests", list_mock)
            mp.setattr("app.api.routes.count_mod_requests", count_mock)
            r = client.get("/v1/mods", params={"offset": 10000})

        assert r.status_code == 200
        # The 200 path DOES set Cache-Control.
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert body["offset"] == 10000
        # Storage helpers were called once.
        assert list_mock.await_count == 1
        assert count_mock.await_count == 1
