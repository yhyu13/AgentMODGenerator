"""HTTP-layer 200/422 contract tests for ``GET /v1/feature_flags/history``.

Closes the **sixth** of eight admin endpoints at the TestClient
layer. The schedule:

  - toggle v152 (422) + v153 (200/404/423)
  - rollback v154 (200/404/409)
  - pin v155 (200/404)
  - unpin v156 (200/404)
  - list v157 (200)
  - **history v158 (200/422) — this file**
  - pin_state, pins list (GET siblings — still to come)

The history endpoint has NO 404 surface but DOES have a 422 surface:

  - No 404: ``get_history()`` is the audit-log query, not a
    registry lookup. An unknown ``flag_name`` returns
    ``entries=[]`` / ``total=0`` rather than raising 404 — the
    same defensive-empty pattern v157 pins for an empty
    registry.
  - 422: ``limit`` is a ``Query(ge=1, le=1000)`` parameter and
    ``flag_name`` has a ``max_length=128`` clamp. Out-of-range
    values are rejected by FastAPI BEFORE the handler runs.
  - No 409: the endpoint is a GET — non-mutating.

The seven cases pinned here cover:

  1. **Happy path with three events** — three audit-log rows,
     no filter, default ``limit``. Response carries all three
     in newest-first order. ``total == len(entries) == 3``.
  2. **Empty audit log** — ``get_history()`` returns ``[]``;
     response is ``{"entries": [], "total": 0}`` (NOT 404).
  3. **``flag_name`` filter** — handler forwards ``flag_name``
     to ``get_history(name=...)`` and ``total`` reflects the
     FILTERED count.
  4. **``limit`` clamps ``entries`` but NOT ``total``** — five
     events with ``limit=2`` → ``len(entries) == 2`` BUT
     ``total == 5``. Load-bearing contract: ``total`` is "rows
     that matched", not "rows in the rolling buffer".
  5. **422 for ``limit=0``** — ``Query(ge=1)`` rejects it
     before the handler runs.
  6. **422 for ``limit=1001``** — ``Query(le=1000)`` rejects
     it before the handler runs.
  7. **422 for ``flag_name`` length > 128** —
     ``Query(max_length=128)`` rejects it before the handler
     runs.

The handler uses a deferred import (``from
orchestrator.feature_flags import get_history`` inside the
handler body, ``app/api/routes.py`` line 1421), so
``monkeypatch.setattr`` at the module attribute level binds
correctly at handler-invocation time. Same deferred-import
trick v152 + v153 + v154 + v155 + v156 + v157 used.

``get_history`` is a sync function (``def get_history(name: str |
None = None) -> list[FlagOverride]`` at
``orchestrator/feature_flags.py``), so ``MagicMock`` is the
correct mock type — not ``AsyncMock``. The handler is
``async def`` but calls ``get_history`` synchronously (no
``await``); pytest's TestClient handles the
async-handler-in-sync-test seam automatically.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from orchestrator.feature_flags import FlagOverride


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _make_event(name: str, value: bool, reason: str, actor: str) -> FlagOverride:
    """Build a FlagOverride for test fixtures.

    Mirrors the helper in ``test_get_feature_flags_history.py``
    (the handler-direct coverage round). Module-level function
    rather than a fixture because every test constructs its own
    bespoke events; a fixture would just add an indirection
    without saving any code.
    """
    return FlagOverride(name=name, value=value, reason=reason, actor=actor)


class TestHistoryFeatureFlagEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/feature_flags/history``."""

    def test_happy_path_three_events_newest_first(
        self, client: TestClient,
    ) -> None:
        """Three audit-log rows, no filter, default ``limit``.
        Response carries all three in newest-first order
        (``get_history`` already returns newest-first; the
        handler does NOT re-reverse). ``total`` matches
        ``len(entries)``.

        Pins the FastAPI status-code mapping (200), the JSON
        content-type, the wire shape of every entry
        (``name`` / ``value`` / ``reason`` / ``actor``), and
        the handler's forwarding of ``get_history(name=None)``
        when no ``flag_name`` query parameter is supplied.
        """
        events = [
            _make_event("flag_c", False, "rollback", "alice"),
            _make_event("flag_b", True, "pin_flag", "bob"),
            _make_event("flag_a", True, "set_flag", "system"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            get_history.return_value = events
            r = client.get("/v1/feature_flags/history")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # JSON content-type — dashboards key off this header.
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert body["total"] == 3
        assert len(body["entries"]) == 3
        assert [e["name"] for e in body["entries"]] == [
            "flag_c",
            "flag_b",
            "flag_a",
        ]
        assert [e["value"] for e in body["entries"]] == [
            False,
            True,
            True,
        ]
        assert [e["reason"] for e in body["entries"]] == [
            "rollback",
            "pin_flag",
            "set_flag",
        ]
        assert [e["actor"] for e in body["entries"]] == [
            "alice",
            "bob",
            "system",
        ]
        # ``get_history`` is called exactly once with kwarg
        # ``name=None``. A refactor that called it positionally
        # (``call(None)``) would still pass the result
        # assertions but fail this call_args pin.
        get_history.assert_called_once_with(name=None)

    def test_empty_audit_log_returns_200_with_empty_entries(
        self, client: TestClient,
    ) -> None:
        """When ``get_history()`` returns an empty list (a
        fresh process), the endpoint returns ``entries=[]``
        and ``total=0`` rather than raising or returning 404.

        Defensive-empty pattern, identical to
        ``FeatureFlagsResponse`` for an empty registry. The
        audit log is a query, not a registry lookup, so "no
        rows match" is a legitimate empty result — NOT 404.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            get_history.return_value = []
            r = client.get("/v1/feature_flags/history")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body == {"entries": [], "total": 0}
        get_history.assert_called_once_with(name=None)

    def test_flag_name_filter_returns_only_matching_events(
        self, client: TestClient,
    ) -> None:
        """When ``flag_name`` is supplied, the handler forwards
        it to ``get_history(name=...)`` and the response
        surfaces the FILTERED list. ``total`` reflects the
        filtered count, NOT the pre-filter count.

        Pin the load-bearing contract: ``total`` is "rows that
        matched", not "rows in the rolling buffer". Without
        this pin, a refactor that called ``get_history()``
        (un-filtered) and then filtered in-memory before
        computing ``total`` would be wrong — the rolling
        buffer is capped at 100 rows, so a "show me all
        events for flag X" query that happened to have MORE
        than 100 events in the buffer would silently
        truncate.
        """
        filtered = [
            _make_event("flag_a", True, "set_flag", "system"),
            _make_event("flag_a", False, "rollback", "alice"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            get_history.return_value = filtered
            r = client.get(
                "/v1/feature_flags/history",
                params={"flag_name": "flag_a"},
            )

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # ``flag_name`` is forwarded as the kwarg, not
        # positional. A refactor that called
        # ``get_history("flag_a")`` would still pass the
        # result assertions below but fail this call_args
        # pin.
        get_history.assert_called_once_with(name="flag_a")
        body = r.json()
        assert body["total"] == 2
        assert len(body["entries"]) == 2
        assert all(e["name"] == "flag_a" for e in body["entries"])
        assert [e["value"] for e in body["entries"]] == [True, False]

    def test_limit_clamps_entries_but_not_total(
        self, client: TestClient,
    ) -> None:
        """The handler slices ``history[:limit]`` for ``entries``
        but computes ``total`` BEFORE the limit is applied.
        Five events with ``limit=2`` → ``len(entries) == 2``
        BUT ``total == 5``.

        This is the most important behaviour contract of the
        endpoint and the schema docstring says so explicitly:
        ``total`` lets a caller detect that the history has
        grown past the page size. A regression that set
        ``total = len(page)`` would silently break
        pagination-detection for callers who use ``total``
        to detect "history has grown past the page size".
        """
        events = [
            _make_event("flag_e", True, "set_flag", "system"),
            _make_event("flag_d", True, "set_flag", "system"),
            _make_event("flag_c", False, "rollback", "alice"),
            _make_event("flag_b", True, "set_flag", "system"),
            _make_event("flag_a", True, "set_flag", "system"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            get_history.return_value = events
            r = client.get(
                "/v1/feature_flags/history",
                params={"limit": 2},
            )

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        # ``total`` reflects the FULL filtered count, NOT the
        # post-limit page size. Pin the schema docstring's
        # explicit promise.
        assert body["total"] == 5, (
            "total must reflect the FULL filtered count, NOT "
            "the post-limit page size"
        )
        assert len(body["entries"]) == 2
        # The page is the FIRST N (newest), so we expect
        # flag_e then flag_d.
        assert [e["name"] for e in body["entries"]] == [
            "flag_e",
            "flag_d",
        ]


class TestHistoryFeatureFlagEndpoint422:
    """422 validation-error contract for ``GET /v1/feature_flags/history``.

    FastAPI's ``Query`` validator rejects out-of-range values
    BEFORE the handler runs. The response body is FastAPI's
    default validation-error envelope (a ``detail`` array of
    ``{"type", "loc", "msg", ...}`` objects). Pin the
    validation surface so a refactor that drops the constraints
    or replaces the ``Query`` annotations with plain types
    surfaces here.
    """

    def _assert_limit_422(self, client: TestClient, limit_value: int) -> None:
        """Shared assertion for ``limit`` out-of-range cases.

        Verifies (a) status code is 422, (b) ``get_history``
        was NOT called (FastAPI rejects BEFORE handler
        dispatch), and (c) the response body's ``detail``
        array carries a ``limit`` entry in ``loc``.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            r = client.get(
                "/v1/feature_flags/history",
                params={"limit": limit_value},
            )

        assert r.status_code == 422, (
            f"expected 422 for limit={limit_value}, "
            f"got {r.status_code}: {r.text!r}"
        )
        # The handler must NOT have been reached — the Query
        # validator fires before handler dispatch. A regression
        # that moved the validation into the handler body
        # would still return 422 but would call
        # ``get_history`` first; pin ``assert_not_called`` to
        # catch that.
        get_history.assert_not_called()
        body = r.json()
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert any(
            "limit" in str(entry.get("loc", []))
            for entry in body["detail"]
        )

    def test_limit_zero_returns_422(self, client: TestClient) -> None:
        """``limit=0`` violates the ``ge=1`` validator.

        Pin the lower-bound surface explicitly so a refactor
        that loosened ``ge=1`` to ``ge=0`` would surface
        here as ``limit=0`` returning 200.
        """
        self._assert_limit_422(client, 0)

    def test_limit_above_max_returns_422(self, client: TestClient) -> None:
        """``limit=1001`` violates the ``le=1000`` validator.

        Pin the upper-bound surface explicitly so a refactor
        that loosened ``le=1000`` to ``le=10000`` would
        surface here as ``limit=1001`` returning 200.
        """
        self._assert_limit_422(client, 1001)

    def test_flag_name_too_long_returns_422(self, client: TestClient) -> None:
        """``flag_name`` has a ``max_length=128`` clamp. A
        129-character name is rejected by FastAPI with 422
        BEFORE the handler runs.

        Pin the ``flag_name`` length-validation surface so a
        refactor that removed the ``max_length=128`` clamp
        (or replaced the ``Query`` annotation with a plain
        ``str | None`` type) would surface here as a 200
        response with an oversized query parameter.
        """
        long_name = "a" * 129
        with pytest.MonkeyPatch.context() as mp:
            get_history = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_history",
                get_history,
            )
            r = client.get(
                "/v1/feature_flags/history",
                params={"flag_name": long_name},
            )

        assert r.status_code == 422, (
            f"expected 422 for flag_name=129chars, "
            f"got {r.status_code}: {r.text!r}"
        )
        get_history.assert_not_called()
        body = r.json()
        assert "detail" in body
        assert any(
            "flag_name" in str(entry.get("loc", []))
            for entry in body["detail"]
        )