"""HTTP-layer 200 contract tests for ``GET /v1/mods/cancellation_reasons``.

Closes the **third of eight** Session 1 introspection endpoints at
the TestClient layer. The schedule (v162 cron update):

  - list_mods           — TestClient (v164 ``test_list_mods_endpoint.py``)
  - get_mod_stats       — TestClient (v163 ``test_mod_stats_endpoint.py``)
  - **list_cancellation_reasons — TestClient (this file) — third
                            TestClient coverage for a Session 1 endpoint**
  - get_cancellation_reason_endpoint — handler-direct only
    (test_cancellation_reason_endpoint.py)
  - list_generators / list_phases / list_known_phases /
    get_phase_detail — handler-direct only
  - (Session 3 sub-resources + Session 4 packs/route_preview all
    still handler-direct only — future cron picks)

This is the simplest of the eight Session 1 endpoints:

  - **No path param, no query params.** The handler signature is
    ``async def list_cancellation_reasons() -> CancellationReasonsListResponse``
    (routes.py:662). It takes no input from the request.
  - **No storage call, no Redis call, no DB call.** The handler
    reads ``KNOWN_CANCELLATION_REASONS`` (a module-level
    ``frozenset[str]`` defined at routes.py:86-93) and sorts it.
    The only "external" dependency is the canonical set, which is
    baked into the module at import time.
  - **No auth.** No ``Depends(verify_api_key)`` on the decorator
    (routes.py:661), so the endpoint is unauthenticated by design.
  - **No path-order trap for TestClient.** The endpoint is
    registered before ``/mods/{request_id}`` (the comment at
    routes.py:681-685 calls this out explicitly — FastAPI's
    path matching is declaration-order sensitive, so a request
    to ``/v1/mods/cancellation_reasons`` would otherwise be
    captured by the generic ``{request_id}`` route).

That simplicity is exactly what makes this round's TestClient
seam cheap and high-leverage: every wire-level property is
visible to the TestClient without any patching needed, because
the canonical set is a module attribute. We only patch
``KNOWN_CANCELLATION_REASONS`` when we want a non-production
state for a specific test case.

Wire properties pinned here (the contract surface that ONLY the
TestClient layer can see):

  1. **Happy path 200** — the response body is
     ``{"reasons": [...sorted canonical set...], "count": N}``
     with ``Content-Type: application/json`` and the
     ``Cache-Control: no-store`` header (the path is under
     ``/v1/mods/``, so the middleware at ``app/middleware.py``
     lines 47 + 190-191 stamps the no-store default).
  2. **Sort order** — the wire list is sorted ascending,
     not the ``frozenset`` declaration order. The handler
     calls ``sorted(KNOWN_CANCELLATION_REASONS)`` (routes.py:687)
     so the wire contract is "ascending lex order", independent
     of the frozenset's hash order.
  3. **Count consistency** — ``body["count"]`` matches
     ``len(body["reasons"])``. The handler computes
     ``count=len(reasons)`` from the same sorted list, so
     these two are tied at the source — but a regression that
     miscounted (e.g. used ``len(KNOWN_CANCELLATION_REASONS)``
     before sorting) would surface here.
  4. **Empty set** — if the canonical set is empty (e.g. a
     code-path that filters to a subset), the wire shape is
     still 200 with ``reasons=[]`` and ``count=0``, NOT 404.
     Same defensive-empty pattern as the v163 stats test.
  5. **Custom set** — the handler honours the live
     ``KNOWN_CANCELLATION_REASONS`` module attribute, not a
     hard-coded copy. We patch the attribute to a synthetic
     set (``{"alpha", "beta", "gamma"}``) and confirm the
     wire shape reflects the patched set. This pins the
     "no-copy" property: a future refactor that copies the
     set at handler-call time (e.g. ``_reasons = list(
     KNOWN_CANCELLATION_REASONS)`` at module top-level)
     would still work, but a refactor that hard-codes the
     canonical list inline would surface as a wire mismatch
     here.
  6. **Pydantic schema rejects non-list reasons** — the schema
     ``CancellationReasonsListResponse`` (schemas.py:80) declares
     ``reasons: list[str]`` and ``count: int``. A regression
     that swapped the types would surface at the schema layer
     before the HTTP layer. We don't drive this through the
     TestClient (the handler always produces a valid list);
     the v77 ``test_cancellation_reasons.py`` already pins the
     schema-level invariants.

The ``KNOWN_CANCELLATION_REASONS`` set on master is a
``frozenset[str]`` of ``{"user_cancelled", "timeout",
"t2_failed", "t1_failed", "content_filter", "llm_error"}``
(routes.py:86-93). That's 6 reasons; the wire response in
the production test must have ``count == 6`` and
``reasons == sorted({...})``.

Why this matters in production: a chat bot that wants to
render a "why was my request cancelled?" picker iterates
``reasons`` in order; a dashboard that shows the count
("5 of these were cancelled with reason X") reads
``count``. A regression that swapped sorted() for
shuffled() (or worse, returned the frozenset in hash order)
would break the chat-bot UX but would not surface in the
``test_cancellation_reasons.py`` handler-direct tests —
the TestClient seam is the only place where the *on-the-wire
list order* is observable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import KNOWN_CANCELLATION_REASONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """TestClient against the running FastAPI app.

    The cancellation_reasons endpoint is unauthenticated by design
    (no ``Depends(verify_api_key)`` on the route decorator at
    routes.py:661), so no auth header is required. The TestClient
    spins up the real ``app`` object, which exercises the full
    route-registration chain (prefix ``/v1``, the
    ``@router.get("/mods/cancellation_reasons", ...)`` decorator,
    and the ``response_model=CancellationReasonsListResponse``
    model serializer).
    """
    return TestClient(app)


# ---------------------------------------------------------------------------
# Happy path — 200 with the production canonical set
# ---------------------------------------------------------------------------

class TestListCancellationReasonsEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/mods/cancellation_reasons``."""

    def test_production_canonical_set_returns_200_with_sorted_list(
        self, client: TestClient,
    ) -> None:
        """The wire response is the production canonical set,
        sorted ascending, with ``count == len(reasons)``,
        ``Content-Type: application/json``, and the
        ``Cache-Control: no-store`` header (the middleware at
        ``app/middleware.py`` lines 47 + 190-191 stamps it
        because the path is under ``/v1/mods/``).

        This is the no-patch test — it exercises the full route
        registration against the real ``KNOWN_CANCELLATION_REASONS``
        module attribute. A regression that hard-codes a different
        set inline would surface here as a wire mismatch.
        """
        r = client.get("/v1/mods/cancellation_reasons")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # JSON content-type — the endpoint is a JSON API and
        # chat bots / dashboards key off this header.
        assert r.headers["content-type"].startswith("application/json")
        # Cache-Control: no-store is set by the middleware
        # (app/middleware.py line 191) because the path is under
        # /v1/mods/ (line 47 prefix). Pin that the middleware
        # actually fires on this path — a regression that
        # tightened the prefix matcher would surface as the
        # header being missing.
        assert r.headers.get("cache-control") == "no-store"

        body = r.json()
        # Top-level fields are all present (no extras, no missing).
        assert set(body.keys()) == {"reasons", "count"}
        # The wire list is sorted ascending. This is the load-bearing
        # wire property: chat bots iterate ``reasons`` in order
        # to render a picker, and a regression that returned
        # frozenset hash order would break that UX.
        assert body["reasons"] == sorted(body["reasons"])
        # The list matches the production canonical set, sorted.
        expected = sorted(KNOWN_CANCELLATION_REASONS)
        assert body["reasons"] == expected
        # ``count`` is consistent with the list length.
        # The handler computes ``count=len(reasons)`` (routes.py:689)
        # from the same sorted list, so these are tied at the
        # source. A regression that miscounted would surface here.
        assert body["count"] == len(body["reasons"])
        # The set is non-empty — there must be at least one
        # canonical reason (the branch uses ``"user_cancelled"``
        # at minimum; see the v77 test_cancellation_reasons.py
        # handler-direct test for the parallel assertion).
        assert body["count"] >= 1

    def test_wire_list_contains_user_cancelled(
        self, client: TestClient,
    ) -> None:
        """The ``user_cancelled`` reason must be in the wire list.

        The cancel handler in the discord-ops-hardening branch
        writes ``"user_cancelled"`` as the reason key. If the
        canonical set ever drops that key, the cancel handler
        would still write it (Redis doesn't validate), but the
        ``/v1/mods/cancellation_reasons`` endpoint would lie
        about a value the server is actively writing — a
        client-driven "valid reason" picker would omit the
        reason the server uses most often.

        Pin the invariant at the HTTP layer so a future
        refactor that shrinks the canonical set surfaces here
        before it ships.
        """
        r = client.get("/v1/mods/cancellation_reasons")
        assert r.status_code == 200
        body = r.json()
        assert "user_cancelled" in body["reasons"]
        # And the count is the length of the returned list, not
        # a stale constant.
        assert body["count"] == len(body["reasons"])

    def test_wire_list_has_no_duplicates(
        self, client: TestClient,
    ) -> None:
        """Defensive: the wire list has no duplicate strings.

        The handler sorts a ``frozenset`` (routes.py:687), so
        duplicates are already impossible. But a regression
        that switched the source to a ``list`` and forgot to
        dedupe would surface here.
        """
        r = client.get("/v1/mods/cancellation_reasons")
        body = r.json()
        assert len(body["reasons"]) == len(set(body["reasons"]))

    def test_wire_list_values_are_strings(
        self, client: TestClient,
    ) -> None:
        """Every reason value is a non-empty ``str``.

        ``CancellationReasonsListResponse.reasons`` is typed
        ``list[str]`` (schemas.py:96) — a regression that
        let an enum's ``.name`` leak (returning ``IntEnum``
        members) would surface as a 422 at the Pydantic
        validation step, but pinning the type at the HTTP
        layer is cheap.
        """
        r = client.get("/v1/mods/cancellation_reasons")
        body = r.json()
        for reason in body["reasons"]:
            assert isinstance(reason, str), (
                f"non-string reason in wire list: {reason!r}"
            )
            assert reason, "empty-string reason in wire list"


# ---------------------------------------------------------------------------
# Defensive-empty pattern — 200 with an empty set, NOT 404
# ---------------------------------------------------------------------------

class TestListCancellationReasonsEndpointEmpty:
    """When the canonical set is empty, the endpoint returns
    200 with ``reasons=[]`` and ``count=0`` — NOT 404.

    Same defensive-empty pattern as the v163 stats test
    (``test_empty_registry_returns_empty_lists``) and the
    v164 list_mods test (the empty-page 200 case)."""

    def test_empty_canonical_set_returns_200_not_404(
        self, client: TestClient,
    ) -> None:
        """Patching the module attribute to an empty frozenset
        must still produce 200 with an empty list — the
        endpoint is "list the canonical set", not "is the
        canonical set non-empty?". A regression that raised
        404 on empty would break clients that key off the
        endpoint as a "is this reason still valid?" oracle
        (a deleted reason should surface as a missing entry
        in the list, not a 404 on the endpoint itself).
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.api.routes.KNOWN_CANCELLATION_REASONS",
                frozenset(),
            )
            r = client.get("/v1/mods/cancellation_reasons")

        assert r.status_code == 200, (
            f"expected 200 for empty set, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["reasons"] == []
        assert body["count"] == 0
        # Cache-Control: no-store is still set — the middleware
        # fires on path match, not on body content.
        assert r.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# Live-attribute pinning — the handler honours the live module attribute
# ---------------------------------------------------------------------------

class TestListCancellationReasonsEndpointLiveAttribute:
    """The handler reads ``KNOWN_CANCELLATION_REASONS`` at call
    time (line 687: ``reasons = sorted(KNOWN_CANCELLATION_REASONS)``),
    not at module-import time. A future refactor that cached the
    sorted list at module top-level (e.g. ``_SORTED_REASONS =
    sorted(KNOWN_CANCELLATION_REASONS)``) would still work for
    the production set, but would freeze the list at import
    time — a code-path that mutates the canonical set after
    import (e.g. a future "add a new reason at startup" feature)
    would not see the new value. Pin that the handler reads
    the live attribute, not a cached copy."""

    def test_handler_reads_live_module_attribute(
        self, client: TestClient,
    ) -> None:
        """Patching the module attribute to a synthetic set
        ``{"alpha", "beta", "gamma"}`` produces a wire
        response with exactly those three reasons (sorted:
        ``["alpha", "beta", "gamma"]``) and ``count=3``.

        This is the "no-cached-copy" pin: a refactor that
        captured the set at module-import time would not
        see the patched value, and the wire would still
        reflect the production canonical set (or a stale
        snapshot of it).
        """
        synthetic = frozenset({"gamma", "alpha", "beta"})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.api.routes.KNOWN_CANCELLATION_REASONS",
                synthetic,
            )
            r = client.get("/v1/mods/cancellation_reasons")

        assert r.status_code == 200
        body = r.json()
        # The synthetic set is echoed on the wire, sorted.
        assert body["reasons"] == ["alpha", "beta", "gamma"]
        assert body["count"] == 3

    def test_handler_sorts_unsorted_input(
        self, client: TestClient,
    ) -> None:
        """Patching the module attribute to an unsorted
        set ``{"zebra", "apple", "mango"}`` produces a wire
        response with the lex-sorted list
        ``["apple", "mango", "zebra"]``.

        This pins the ``sorted()`` call at routes.py:687
        independently of the production canonical set's
        order. A regression that dropped the ``sorted()``
        call and returned ``list(KNOWN_CANCELLATION_REASONS)``
        directly would surface here as the wire list
        being in frozenset hash order (not lex order).
        """
        unsorted = frozenset({"zebra", "apple", "mango"})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.api.routes.KNOWN_CANCELLATION_REASONS",
                unsorted,
            )
            r = client.get("/v1/mods/cancellation_reasons")

        assert r.status_code == 200
        body = r.json()
        assert body["reasons"] == ["apple", "mango", "zebra"]
        assert body["count"] == 3
