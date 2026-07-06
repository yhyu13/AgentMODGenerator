"""HTTP-layer 200/304 contract tests for ``GET /v1/mods/stats``.

Closes the **first of eight** Session 1 introspection endpoints at
the TestClient layer. The schedule (v162 cron update):

  - list_mods        — handler-direct only (v30 ``test_list_mods.py``)
  - **get_mod_stats  — TestClient (this file) — first TestClient
                       coverage for a Session 1 endpoint**
  - cancellation_reasons — handler-direct only
  - list_generators / list_phases / list_known_phases /
    get_phase_detail — handler-direct only
  - (Session 3 sub-resources + Session 4 packs/route_preview all
    still handler-direct only — future cron picks)

The stats endpoint has TWO observable HTTP behaviours that the
TestClient layer must pin separately from the handler-direct
seam:

  1. **200 happy path** with the ``StatsResponse`` wire shape
     (``total`` + ``by_status`` + ``by_phase`` + ``generated_at``)
     and the ``ETag`` header (RFC 7232 double-quoted sha256 of
     the stable projection).
  2. **304 short-circuit** when the request carries an
     ``If-None-Match`` header that matches the current ETag
     — the response has no body, just the ETag header. The
     v77 F2 change in the route docstring calls this out
     explicitly.

Both are tested here at the TestClient layer using the same
``monkeypatch.setattr`` trick the v152-v160 feature-flag
TestClient files use: ``get_mod_request_stats`` is imported at
module-top-level in ``app/api/routes.py`` (line 73), so patching
the attribute on ``app.api.routes`` binds correctly at
handler-invocation time.

``get_mod_request_stats`` is an ``async def`` function
(``storage.queries.py`` line 340, returns ``dict[str, Any]``),
so the correct mock type is ``AsyncMock`` — NOT ``MagicMock``.
The handler awaits it (line 2258: ``raw = await
get_mod_request_stats()``), so a sync ``MagicMock`` would fail
the coroutine-await boundary. This is the opposite of the
feature-flag TestClient files (v152-v160) which patch sync
helpers with ``MagicMock``; the rationale is captured at the
top of the test classes that use each mock type.

The handler's stable-projection hash excludes ``generated_at``
(line 2280-2282 docstring + line 2283-2287 code) — so the
ETag is deterministic across calls even though ``generated_at``
advances on every request. This is the load-bearing property
the 304 path tests rely on: the ETag computed at request 1
must match the ETag computed at request 2 if the breakdown
data is unchanged, even though the timestamps differ.

Five test cases pinned here:

  1. **Happy path** — three statuses, three phases, ``total`` is
     the sum of the per-status counts, ``by_status`` and
     ``by_phase`` are populated, ``generated_at`` is a recent
     ISO timestamp, the ETag header is a quoted sha256.
  2. **Empty registry** — ``get_mod_request_stats`` returns
     ``{"total": 0, "by_status": [], "by_phase": []}``; the
     response is the same shape with empty lists. Defensive
     pattern, NOT 404.
  3. **Phase with NULL surfaces as ``__none__``** — the storage
     helper returns a row with ``phase="__none__"``; the
     response echoes it verbatim. The route does NOT transform
     the synthetic key; it just trusts the helper's contract.
  4. **304 on matching ETag** — compute the ETag by hand from
     the helper's return dict, pass it via ``If-None-Match``,
     confirm the response is 304 with no body and the ETag
     header echoed back.
  5. **304 also matches unquoted ETag** — some proxies strip
     the wrapping quotes; the handler accepts both formats
     (line 2293). Pin that contract at the HTTP layer.

The endpoint requires NO auth header (operator dashboard
endpoint, like every other admin GET) and the v77 ETag is
deterministic without a real DB — the helper mock is the
single source of truth for what the response computes from.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient against the running FastAPI app.

    The stats endpoint is unauthenticated by design (per the
    route docstring: "no filters, no pagination, no auth —
    this is a global operator view"), so no auth header is
    required.
    """
    return TestClient(app)


def _expected_etag(stats_dict: dict) -> str:
    """Compute the handler's ETag from a helper-returned dict.

    Mirrors the handler's stable-projection hash at
    ``app/api/routes.py`` lines 2283-2289: sha256 of
    ``json.dumps(stable_projection, sort_keys=True).encode("utf-8")``
    where ``stable_projection`` is the data fields ONLY
    (``total`` + ``by_status`` + ``by_phase``), with
    ``generated_at`` excluded. The handler wraps the hex digest
    in double quotes per RFC 7232.
    """
    stable_projection = {
        "total": stats_dict.get("total", 0),
        "by_status": stats_dict.get("by_status", []),
        "by_phase": stats_dict.get("by_phase", []),
    }
    body_bytes = json.dumps(stable_projection, sort_keys=True).encode("utf-8")
    return hashlib.sha256(body_bytes).hexdigest()


class TestModStatsEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/mods/stats``."""

    def test_happy_path_returns_200_with_breakdowns(
        self, client: TestClient,
    ) -> None:
        """Three statuses, three phases, full ``StatsResponse``
        shape, ``Content-Type: application/json``, ``ETag``
        header present and a quoted sha256.

        The handler calls ``get_mod_request_stats`` exactly once
        (no pagination, no filters), the response is the
        ``StatsResponse`` model serialised via ``model_dump(mode="json")``
        (so ``generated_at`` is an ISO string, not a Python
        ``datetime``), and the ETag is the sha256 of the
        stable projection (data fields only, not the timestamp).
        """
        stats = {
            "total": 17,
            "by_status": [
                {"status": "done", "count": 10},
                {"status": "running", "count": 5},
                {"status": "failed", "count": 2},
            ],
            "by_phase": [
                {"phase": "shop_channel", "count": 9},
                {"phase": "weather_event", "count": 5},
                {"phase": "achievements", "count": 3},
            ],
        }
        expected_etag = _expected_etag(stats)

        with pytest.MonkeyPatch.context() as mp:
            get_mod_request_stats = AsyncMock(return_value=stats)
            mp.setattr(
                "app.api.routes.get_mod_request_stats",
                get_mod_request_stats,
            )
            r = client.get("/v1/mods/stats")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # JSON content-type — the endpoint is a JSON API and
        # dashboards key off this header. A regression that
        # dropped FastAPI's default serialiser would surface
        # here as ``text/html``.
        assert r.headers["content-type"].startswith("application/json")
        # ETag header is present and is a quoted sha256.
        assert r.headers["etag"] == f'"{expected_etag}"'

        body = r.json()
        # Top-level fields are all present.
        assert set(body.keys()) >= {
            "total", "by_status", "by_phase", "generated_at",
        }
        assert body["total"] == 17
        # ``by_status`` echoes the helper's rows in order
        # (the helper sorts by count desc; the handler does
        # NOT re-sort).
        assert body["by_status"] == stats["by_status"]
        assert body["by_phase"] == stats["by_phase"]
        # ``generated_at`` is a recent ISO timestamp — within
        # the last 60 seconds of the test's wall clock.
        # We just sanity-check it parses and is a string, not
        # a datetime object (Pydantic's ``model_dump(mode="json")``
        # is what serialises it to ISO 8601).
        assert isinstance(body["generated_at"], str)
        assert "T" in body["generated_at"]  # ISO 8601 separator
        # Helper was called exactly once.
        assert get_mod_request_stats.await_count == 1

    def test_empty_registry_returns_empty_lists(
        self, client: TestClient,
    ) -> None:
        """Empty ``mod_requests`` table — ``total=0`` and both
        breakdowns are empty lists. The endpoint is NOT 404 for
        an empty registry; same defensive-empty pattern that
        ``/v1/feature_flags`` and ``/v1/packs`` use.
        """
        stats = {"total": 0, "by_status": [], "by_phase": []}

        with pytest.MonkeyPatch.context() as mp:
            get_mod_request_stats = AsyncMock(return_value=stats)
            mp.setattr(
                "app.api.routes.get_mod_request_stats",
                get_mod_request_stats,
            )
            r = client.get("/v1/mods/stats")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["by_status"] == []
        assert body["by_phase"] == []
        # ``generated_at`` is still populated — the timestamp
        # is the request time, not the data time, so even an
        # empty registry gets a fresh timestamp.
        assert isinstance(body["generated_at"], str)

    def test_null_phase_surfaces_as_underscore_none(
        self, client: TestClient,
    ) -> None:
        """The synthetic ``__none__`` key for ``phase IS NULL``
        rows is echoed verbatim by the handler. The handler
        does NOT transform it — that's the storage helper's
        job (``_STATS_NULL_PHASE_KEY`` constant in
        ``storage/queries.py``).

        This is the load-bearing contract: a dashboard that
        wants to render a "no phase" row uses ``__none__`` as
        the key directly, and the route preserves the helper's
        naming.
        """
        stats = {
            "total": 4,
            "by_status": [
                {"status": "done", "count": 3},
                {"status": "failed", "count": 1},
            ],
            "by_phase": [
                {"phase": "shop_channel", "count": 3},
                {"phase": "__none__", "count": 1},
            ],
        }

        with pytest.MonkeyPatch.context() as mp:
            get_mod_request_stats = AsyncMock(return_value=stats)
            mp.setattr(
                "app.api.routes.get_mod_request_stats",
                get_mod_request_stats,
            )
            r = client.get("/v1/mods/stats")

        assert r.status_code == 200
        body = r.json()
        # The synthetic key is present in the by_phase list.
        phase_keys = [row["phase"] for row in body["by_phase"]]
        assert "__none__" in phase_keys
        # And the count is the raw integer (not stringified).
        none_row = next(
            row for row in body["by_phase"] if row["phase"] == "__none__"
        )
        assert none_row["count"] == 1


class TestModStatsEndpoint304:
    """``If-None-Match`` short-circuit to 304 (no body, ETag echoed)."""

    def test_matching_etag_returns_304_no_body(
        self, client: TestClient,
    ) -> None:
        """When the ``If-None-Match`` header carries the current
        ETag (RFC 7232 quoted form), the response is ``304``
        with no body and the ETag header echoed back.

        The v77 F2 docstring (route line 2250-2256) is the
        source of truth for this behaviour; the route's
        ETag-from-stable-projection hash (lines 2280-2289)
        is what makes the short-circuit deterministic — the
        ETag is data-only, not timestamp-influenced, so
        back-to-back calls with the same data produce the
        same ETag.
        """
        stats = {
            "total": 5,
            "by_status": [{"status": "done", "count": 5}],
            "by_phase": [{"phase": "shop_channel", "count": 5}],
        }
        expected_etag = _expected_etag(stats)

        with pytest.MonkeyPatch.context() as mp:
            get_mod_request_stats = AsyncMock(return_value=stats)
            mp.setattr(
                "app.api.routes.get_mod_request_stats",
                get_mod_request_stats,
            )
            r = client.get(
                "/v1/mods/stats",
                headers={"If-None-Match": f'"{expected_etag}"'},
            )

        assert r.status_code == 304, (
            f"expected 304, got {r.status_code}: {r.text!r}"
        )
        # 304 has no body — content-length is 0 (or absent).
        assert r.content == b""
        # ETag header IS present on the 304 (RFC 7232 §4.1:
        # the server generating a 304 MUST include the
        # headers that would have been sent on a 200).
        assert r.headers["etag"] == f'"{expected_etag}"'

    def test_unquoted_etag_also_matches(
        self, client: TestClient,
    ) -> None:
        """Some proxies strip the wrapping quotes from the
        ETag header; the handler accepts the unquoted form
        too (route line 2293, comment: "Accept If-None-Match
        with OR without wrapping quotes (some proxies strip
        them)").

        Pin that contract at the HTTP layer — a future
        refactor that tightens the match to quoted-only
        would break clients behind such proxies.
        """
        stats = {
            "total": 2,
            "by_status": [{"status": "done", "count": 2}],
            "by_phase": [{"phase": "shop_channel", "count": 2}],
        }
        unquoted_etag = _expected_etag(stats)  # no quotes

        with pytest.MonkeyPatch.context() as mp:
            get_mod_request_stats = AsyncMock(return_value=stats)
            mp.setattr(
                "app.api.routes.get_mod_request_stats",
                get_mod_request_stats,
            )
            r = client.get(
                "/v1/mods/stats",
                headers={"If-None-Match": unquoted_etag},
            )

        assert r.status_code == 304
        assert r.headers["etag"] == f'"{unquoted_etag}"'
