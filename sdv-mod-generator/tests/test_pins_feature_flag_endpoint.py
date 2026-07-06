"""HTTP-layer 200 contract tests for ``GET /v1/feature_flags/pins``.

Closes the **eighth and final** of eight admin endpoints at the
TestClient layer. The schedule:

  - toggle v152 (422) + v153 (200/404/423)
  - rollback v154 (200/404/409)
  - pin v155 (200/404)
  - unpin v156 (200/404)
  - list v157 (200)
  - history v158 (200/422)
  - pin_state v159 (200/404)
  - **pins list v160 (200) — this file**

The pins list endpoint is the SIMPLEST of the eight admin endpoints
because it has NO 4xx surface whatsoever:

  - No 404 — the handler always returns ``200`` with a (possibly
    empty) collection. An empty collection is rendered as
    ``{"pins": [], "count": 0}`` rather than ``404``, mirroring
    the v15 ``GET /v1/feature_flags`` empty-set contract and the
    v157 ``TestListFeatureFlagEndpoint200::test_empty_registry``
    empty-set pin. Dashboards can render an "empty" state without
    special-casing the error path.
  - No 422 — there are NO request parameters at all (no path
    parameter, no query validators, no request body). FastAPI's
    validator layer is bypassed entirely.
  - No 409 — the endpoint is a GET — non-mutating.

So this v160 file has only ONE test class —
``TestPinsFeatureFlagEndpoint200`` — covering the four sub-cases
that distinguish "the collection endpoint works":

  1. **Happy path: multiple pinned flags, sorted order** —
     three flags pinned, response carries all three in helper
     order (which IS sorted by name because
     ``get_pinned_flags()`` returns ``tuple(sorted(_locked_pins))``),
     and ``count == 3``. The helper-order contract is the
     load-bearing test for dashboard snapshot diffs.
  2. **Empty collection** — ``get_pinned_flags()`` returns
     ``()``; response is ``{"pins": [], "count": 0}`` (NOT 404).
     Pins the defensive-empty contract that distinguishes
     this endpoint from a registry-lookup that would 404.
  3. **Single pinned flag** — one entry, ``count == 1``.
     Pinned-by-construction is implicit; the response has no
     ``pinned`` field (every flag in this list is pinned).
  4. **Mixed on/off values** — pins can be locked at either
     state (locked-on or locked-off). Each entry is a
     ``FeatureFlagPinSummary`` carrying the live
     ``current_value`` from ``is_enabled(name)``. The
     per-flag lookup is what distinguishes this endpoint
     from a static "list of locked names".

Additionally, two global pins run across every test:

  - ``Content-Type: application/json`` — the endpoint is a
    JSON API and dashboards key off this header. A regression
    that dropped FastAPI's default serialiser would surface
    here as ``text/html``.
  - The two helper mocks (``get_pinned_flags`` and
    ``is_enabled``) are sync — they are sync ``def``
    functions on ``orchestrator.feature_flags`` — so the
    correct mock type is ``MagicMock(return_value=...)``
    for ``get_pinned_flags`` and ``MagicMock(side_effect=...)``
    for ``is_enabled``. NOT ``AsyncMock`` — same as v158's
    ``get_history`` patch and v159's ``is_pinned`` /
    ``is_enabled`` patches.

The handler at ``app/api/routes.py`` lines 2090-2188 uses a
body-level ``from orchestrator.feature_flags import
(get_pinned_flags, is_enabled)`` import — same "import inside
the handler body" pattern v152..v159 use.
``monkeypatch.setattr`` on each module attribute binds
correctly at handler-invocation time. The handler is
``async def`` but calls both helpers synchronously (no
``await``); pytest's TestClient handles the
async-handler-in-sync-test seam automatically.

The handler's deferred-import path means a refactor that
imported the helpers at module-top-level rather than inside
the body would still work today, BUT the seam would no
longer be patchable at the
``orchestrator.feature_flags.{get_pinned_flags,is_enabled}``
attribute level — the imports would resolve to whatever
``app.api.routes`` already saw at import time. The body-level
import is what makes the TestClient mock trick work.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, call

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient against the running FastAPI app.

    The pin-state feature-flag admin endpoints are unauthenticated
    by design (per the v15..v44 family of handler docstrings),
    so no auth header is required.
    """
    return TestClient(app)


class TestPinsFeatureFlagEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/feature_flags/pins``."""

    def test_happy_path_three_pinned_sorted_order(
        self, client: TestClient,
    ) -> None:
        """Three flags pinned, response carries all three in
        helper order (sorted by name because
        ``get_pinned_flags()`` returns ``tuple(sorted(_locked_pins))``),
        and ``count == 3``.

        Pins the FastAPI status-code mapping (200), the JSON
        content-type, the wire shape of every entry (``name``
        + ``current_value``), the ``count`` field, and the
        helper-call ordering (per-flag ``is_enabled`` is
        called for every pinned name in helper order).
        """
        pinned_names = ("alpha", "beta", "gamma")
        # Per-name enabled map; missing keys default to False
        # in the handler-direct coverage (the real
        # ``is_enabled`` helper has a deny-by-default
        # fallback). Mirror the v44 handler-direct pattern
        # here so a test that forgets to seed a value fails
        # loudly rather than passing on a stale True.
        enabled = {"alpha": True, "beta": False, "gamma": True}

        with pytest.MonkeyPatch.context() as mp:
            get_pinned_flags = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_pinned_flags",
                get_pinned_flags,
            )
            get_pinned_flags.return_value = pinned_names
            is_enabled = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.is_enabled",
                is_enabled,
            )
            is_enabled.side_effect = lambda name: enabled.get(name, False)
            r = client.get("/v1/feature_flags/pins")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # JSON content-type — dashboards key off this header.
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert body["count"] == 3
        assert len(body["pins"]) == 3
        # Helper-order contract: response mirrors
        # ``get_pinned_flags()`` order. The helper returns a
        # sorted tuple, so the response is sorted-by-name.
        # A refactor that re-sorted the response would break
        # here; a refactor that lost the order (e.g. used a
        # ``set``) would break here too.
        assert [p["name"] for p in body["pins"]] == [
            "alpha",
            "beta",
            "gamma",
        ]
        assert [p["current_value"] for p in body["pins"]] == [
            True,
            False,
            True,
        ]
        # ``get_pinned_flags`` is called exactly once with no
        # arguments (the helper takes no parameters).
        get_pinned_flags.assert_called_once_with()
        # ``is_enabled`` is called once per pinned name, in
        # helper order. A refactor that hoisted ``is_enabled``
        # outside the comprehension would lose the per-flag
        # mapping; a refactor that called it once with the
        # whole list would also lose it.
        is_enabled.assert_has_calls(
            [call("alpha"), call("beta"), call("gamma")],
        )
        assert is_enabled.call_count == 3

    def test_empty_collection_returns_200_with_empty_pins(
        self, client: TestClient,
    ) -> None:
        """When ``get_pinned_flags()`` returns an empty tuple
        (a fresh process, or after every flag has been
        unpinned), the endpoint returns ``pins=[]`` and
        ``count=0`` rather than raising or returning 404.

        Defensive-empty pattern, identical to
        ``FeatureFlagsResponse`` for an empty registry. The
        collection is a query, not a registry lookup, so
        "no flags pinned" is a legitimate empty result —
        NOT 404. Mirrors the
        ``TestListFeatureFlagEndpoint200::test_empty_registry``
        pin at the v15 ``GET /v1/feature_flags`` endpoint.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_pinned_flags = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_pinned_flags",
                get_pinned_flags,
            )
            get_pinned_flags.return_value = ()
            # ``is_enabled`` is also patched to assert that it
            # is NOT called when the collection is empty (the
            # list comprehension has zero iterations).
            is_enabled = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.is_enabled",
                is_enabled,
            )
            r = client.get("/v1/feature_flags/pins")

        assert r.status_code == 200, (
            f"expected 200 for empty collection, got {r.status_code}: "
            f"{r.text!r}"
        )
        body = r.json()
        # Exact wire-shape pin — NOT just ``== []``, the
        # ``count`` field must be present and zero. A
        # regression that omitted ``count`` for the
        # empty-collection case would surface here.
        assert body == {"pins": [], "count": 0}
        get_pinned_flags.assert_called_once_with()
        # No flags → no ``is_enabled`` calls. A refactor
        # that called ``is_enabled`` outside the
        # comprehension (e.g. pre-warmed a cache) would
        # surface here as ``call_count > 0``.
        is_enabled.assert_not_called()

    def test_single_pinned_flag_round_trips(
        self, client: TestClient,
    ) -> None:
        """One flag pinned → one ``FeatureFlagPinSummary``
        entry, ``count == 1``. The single-entry case is
        the boundary between the empty collection (count=0)
        and the multi-entry collection (count>1).

        Mirrors v132's single-flag pin-state snapshot but
        at the collection level: a single-pin process
        behaves like a single-row table.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_pinned_flags = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_pinned_flags",
                get_pinned_flags,
            )
            get_pinned_flags.return_value = ("flag_a",)
            is_enabled = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.is_enabled",
                is_enabled,
            )
            is_enabled.side_effect = lambda name: name == "flag_a"
            r = client.get("/v1/feature_flags/pins")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["count"] == 1
        assert len(body["pins"]) == 1
        assert body["pins"][0]["name"] == "flag_a"
        assert body["pins"][0]["current_value"] is True
        get_pinned_flags.assert_called_once_with()
        is_enabled.assert_called_once_with("flag_a")

    def test_mixed_on_off_values_round_trip(
        self, client: TestClient,
    ) -> None:
        """A pinned flag is locked, not forced-on. Each entry
        carries the LIVE on/off state (``is_enabled(name)``),
        so a pin can be locked-on OR locked-off. Two flags
        pinned with opposite values verifies the per-flag
        lookup — a refactor that conflated "pinned" with
        "on" would silently break here by reporting every
        pin as ``current_value=True``.
        """
        pinned_names = ("on_flag", "off_flag")
        enabled = {"on_flag": True, "off_flag": False}

        with pytest.MonkeyPatch.context() as mp:
            get_pinned_flags = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.get_pinned_flags",
                get_pinned_flags,
            )
            get_pinned_flags.return_value = pinned_names
            is_enabled = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.is_enabled",
                is_enabled,
            )
            is_enabled.side_effect = lambda name: enabled.get(name, False)
            r = client.get("/v1/feature_flags/pins")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["count"] == 2
        # Helper order is the order the response uses.
        assert [p["name"] for p in body["pins"]] == [
            "on_flag",
            "off_flag",
        ]
        # Mixed on/off values per flag — NOT all True.
        assert [p["current_value"] for p in body["pins"]] == [
            True,
            False,
        ]
        # Per-flag ``is_enabled`` lookup in helper order.
        is_enabled.assert_has_calls(
            [call("on_flag"), call("off_flag")],
        )


