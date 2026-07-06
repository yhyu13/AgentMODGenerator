"""HTTP-layer 200/400/404 contract tests for ``GET /v1/mods/{id}/cancellation_reason``.

Closes the **fourth of eight** Session 1 introspection endpoints at
the TestClient layer. The schedule (v162 cron update, refined by
v165's "next" note):

  - list_mods           — TestClient (v164 ``test_list_mods_endpoint.py``)
  - get_mod_stats       — TestClient (v163 ``test_mod_stats_endpoint.py``)
  - list_cancellation_reasons — TestClient (v165 ``test_list_cancellation_reasons_endpoint.py``)
  - **get_cancellation_reason_endpoint — TestClient (this file) — fourth
                            TestClient coverage for a Session 1 endpoint**
  - list_generators / list_phases / list_known_phases /
    get_phase_detail — handler-direct only (future cron picks)
  - (Session 3 sub-resources + Session 4 packs/route_preview all
    still handler-direct only — future cron picks)

This is the **per-request read-side companion** of
``GET /v1/mods/cancellation_reasons`` (the canonical-reason list,
covered at the TestClient layer by v165). The per-request endpoint
returns a 200 with the stored reason, 404 for an unknown request,
or 400 for a request that exists but is not cancelled. The
handler-direct coverage in v68
``test_cancellation_reason_endpoint.py`` already pins the
*contract-within-the-handler* (status check ordering, the
narrow transient-error catch, ``cancellation_reason=None``
fallback for pre-reason-key cancellations). This file covers
the **wire surface that only the TestClient layer can see**:

  1. **Happy path 200** — status="cancelled" + a recorded reason.
     The wire shape is ``{"request_id": ..., "status": "cancelled",
     "cancellation_reason": "<reason>"}`` with
     ``Content-Type: application/json`` and the
     ``Cache-Control: no-store`` header (the path is under
     ``/v1/mods/``, so the middleware at ``app/middleware.py``
     lines 47 + 190-191 stamps the no-store default).
  2. **Null-reason 200** — status="cancelled" but the reason
     key was never written (pre-reason-key legacy
     cancellation). The wire shows ``cancellation_reason: null``
     (the JSON serialisation of Python ``None``). Pins the
     "null field is reserved for cancellations that pre-date
     the reason-key feature" contract from the handler
     docstring (routes.py:705-709).
  3. **404 unknown request** — ``get_status`` returns ``None``,
     handler raises ``HTTPException(404)``. The wire shows a
     FastAPI error envelope ``{"detail": "Request <id> not
     found"}`` with ``Cache-Control: no-store`` (the
     middleware stamps it on every path under ``/v1/mods/``,
     not just 200s). ``get_cancellation_reason`` is NOT
     called — the handler short-circuits at the status check
     (routes.py:714-718).
  4. **400 not-cancelled** — status is something other than
     "cancelled" (parametrized: ``running``, ``done``,
     ``failed``, ``pending``, ``error``). The wire shows a
     FastAPI error envelope ``{"detail": "Request <id> is
     not cancelled (current status: <status>)"}``. The
     detail echoes the current status so a client can tell
     "running" from "done" without a follow-up call. The
     reason-lookup helper is NOT called (the handler
     short-circuits at routes.py:719-726).
  5. **Transient Redis failure on reason lookup → 200 with
     null** — the narrow catch at routes.py:734 swallows
     ``ConnectionError`` (and the TestClient sees the
     documented graceful-degradation contract: status
     succeeded, reason is back-fillable by a follow-up call
     once Redis recovers).
  6. **Path-order trap regression** — the endpoint is
     registered at routes.py:692 with the pattern
     ``/mods/{request_id}/cancellation_reason``, and the
     handler docstring at routes.py:681-685 explicitly
     notes the path-order sensitivity (FastAPI's path
     matching is declaration-order sensitive — but the
     ``{request_id}`` route is registered AFTER the
     cancellation-reason sibling because the latter has
     a more-specific suffix). We pin that
     ``/v1/mods/req-foo/cancellation_reason`` still
     routes to this handler, NOT the generic
     ``/v1/mods/{request_id}`` handler (which would 422
     on the trailing path segment).

Why this matters in production: a chat bot rendering
"why was my request cancelled?" reads
``body["cancellation_reason"]`` from a 200 response, shows
"request not found" on a 404, and shows "request is not
cancelled (current status: running)" on a 400. A
regression that swapped the 200/400 ordering (e.g.
"return 400 when status is cancelled but reason is null")
would break the bot's UX but would not surface in the
v68 handler-direct tests — the TestClient seam is the
only place where the **on-the-wire status codes and JSON
envelope** are observable.

**Mock recipe difference from v163 / v164 / v165.** The
handler uses a *deferred* import pattern:

    from storage.redis import get_cancellation_reason, get_status

inside the function body (routes.py:711). This means the
names are NOT bound at ``app.api.routes`` module
top-level — the v165 ``mp.setattr(
"app.api.routes.KNOWN_CANCELLATION_REASONS", ...)``
recipe (which works for module-level attributes) would
fail because there's no ``app.api.routes.get_status`` to
patch. The correct recipe is to patch the **source
module** of the deferred import:

    mp.setattr("storage.redis.get_status", async_fn)
    mp.setattr("storage.redis.get_cancellation_reason", async_fn)

The deferred ``from storage.redis import ...`` statement
then picks up the patched attributes at handler-invocation
time. This is the same recipe v68
``test_cancellation_reason_endpoint.py`` uses, lifted to
the TestClient layer. Both ``get_status`` and
``get_cancellation_reason`` are async functions (their
definitions are in ``storage/redis.py``), so the correct
mock type is ``AsyncMock`` — NOT ``MagicMock``. The
handler awaits both (routes.py:713 and routes.py:733), so
a sync ``MagicMock`` would fail the coroutine-await
boundary.

The endpoint requires NO auth header (operator
dashboard-style read of a per-request resource, like
``GET /v1/mods/{id}/status`` and ``GET /v1/mods/{id}/files``).
The path param ``request_id`` is a free-form ``str`` (no
path-validator constraints), so an empty-string
``request_id`` routes to the handler and the handler
passes the empty string through to ``get_status`` /
``get_cancellation_reason`` — this is the
"empty-tenant" semantic and is captured in
``test_request_id_round_trip_in_wire_response`` below.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """TestClient against the running FastAPI app.

    The cancellation_reason endpoint is unauthenticated by design
    (no ``Depends(verify_api_key)`` on the route decorator at
    routes.py:692), so no auth header is required. The TestClient
    spins up the real ``app`` object, which exercises the full
    route-registration chain (prefix ``/v1``, the
    ``@router.get("/mods/{request_id}/cancellation_reason", ...)``
    decorator, and the ``response_model=CancellationReasonResponse``
    model serializer).
    """
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers — mock-factory for the deferred-import storage helpers
# ---------------------------------------------------------------------------

def _patch_redis_helpers(
    mp: pytest.MonkeyPatch,
    *,
    status_value,
    reason_value,
    expect_reason_call: bool = True,
):
    """Patch ``storage.redis.get_status`` and
    ``storage.redis.get_cancellation_reason`` for the duration of a
    TestClient request.

    The handler imports both names from ``storage.redis`` inside
    its function body (deferred import, routes.py:711), so the
    patches must target the source module — patching
    ``app.api.routes.get_status`` would fail because the name
    isn't bound at the routes module level. This is the deferred-
    import recipe from v68 ``test_cancellation_reason_endpoint.py``
    applied to the TestClient layer.

    Returns the two mock callables so the test can assert on
    ``await_count`` / call args (e.g. the handler forwards the
    request_id verbatim to both helpers).
    """
    get_status = AsyncMock(return_value=status_value)
    mp.setattr("storage.redis.get_status", get_status)

    if expect_reason_call:
        get_cancellation_reason = AsyncMock(return_value=reason_value)
    else:
        # If the handler is short-circuited (404 unknown request,
        # 400 not-cancelled), the reason lookup should NOT be
        # attempted. We attach a raising AsyncMock so any future
        # regression that calls get_cancellation_reason when it
        # shouldn't surfaces as a test failure (the un-awaited
        # RuntimeError would propagate up through the handler's
        # narrow catch — which DOES NOT include RuntimeError on
        # the awaited branch; it only catches ConnectionError /
        # asyncio.TimeoutError / RuntimeError on the reason
        # lookup, not the short-circuit branch). Pinning the
        # "not called" invariant catches the regression directly.
        get_cancellation_reason = AsyncMock(
            side_effect=AssertionError(
                "get_cancellation_reason should NOT be called when "
                "status is missing or non-cancelled"
            ),
        )
    mp.setattr(
        "storage.redis.get_cancellation_reason",
        get_cancellation_reason,
    )
    return get_status, get_cancellation_reason


# ---------------------------------------------------------------------------
# Happy path — 200 with a recorded reason
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/mods/{id}/cancellation_reason``."""

    def test_happy_path_returns_200_with_stored_reason(
        self, client: TestClient,
    ) -> None:
        """Status="cancelled" + a recorded reason → 200 with the
        wire shape ``{"request_id": ..., "status": "cancelled",
        "cancellation_reason": "<reason>"}``.

        Pins the on-the-wire shape that the v68 handler-direct
        tests can't see (``Content-Type`` header,
        ``Cache-Control: no-store`` middleware header, JSON
        envelope keys). The handler calls ``get_status`` first
        and ``get_cancellation_reason`` second, both with the
        same ``request_id`` from the path — we assert that
        forwarding.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_status, get_cancellation_reason = _patch_redis_helpers(
                mp,
                status_value="cancelled",
                reason_value="user_cancelled",
            )
            r = client.get("/v1/mods/req-cancel-1/cancellation_reason")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        # JSON content-type — the endpoint is a JSON API and
        # chat bots / dashboards key off this header.
        assert r.headers["content-type"].startswith("application/json")
        # Cache-Control: no-store is set by the middleware
        # (app/middleware.py line 191) because the path is under
        # /v1/mods/ (line 47 prefix).
        assert r.headers.get("cache-control") == "no-store"

        body = r.json()
        # Top-level fields are all present (no extras, no missing).
        assert set(body.keys()) == {
            "request_id", "status", "cancellation_reason",
        }
        # The request_id round-trips — the handler passes the
        # path param through verbatim (routes.py:711-744).
        assert body["request_id"] == "req-cancel-1"
        # The status is always the literal "cancelled" on success
        # — the schema's Literal["cancelled"] is enforced at
        # Pydantic validation time (schemas.py:115-117).
        assert body["status"] == "cancelled"
        # The reason is the value the storage helper returned.
        assert body["cancellation_reason"] == "user_cancelled"

        # Both helpers were called exactly once each, with the
        # request_id from the path. The handler does NOT cache
        # the request_id — it's awaited from the path on every
        # call. A regression that stored the path in a module-
        # level variable would surface here as a call-args
        # mismatch.
        assert get_status.await_count == 1
        assert get_status.await_args is not None
        assert get_status.await_args.args == ("req-cancel-1",)
        assert get_cancellation_reason.await_count == 1
        assert get_cancellation_reason.await_args is not None
        assert get_cancellation_reason.await_args.args == ("req-cancel-1",)

    def test_wire_envelope_with_alternate_reason(
        self, client: TestClient,
    ) -> None:
        """A non-``user_cancelled`` reason (e.g. ``timeout``) surfaces
        on the wire verbatim. Pins that the handler does NOT validate
        the reason against the canonical set — it's a free-form
        string from storage. (The /v1/mods/cancellation_reasons
        endpoint exposes the canonical set for client-side
        validation; the per-request endpoint is a passthrough.)

        Picks ``"timeout"`` specifically because it's one of the
        six canonical reasons on master (routes.py:88) and a
        well-formed non-default value — verifies the wire is
        value-agnostic.
        """
        with pytest.MonkeyPatch.context() as mp:
            _patch_redis_helpers(
                mp,
                status_value="cancelled",
                reason_value="timeout",
            )
            r = client.get("/v1/mods/req-timeout/cancellation_reason")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "cancelled"
        assert body["cancellation_reason"] == "timeout"
        assert body["request_id"] == "req-timeout"


# ---------------------------------------------------------------------------
# Pre-reason-key legacy cancellation — 200 with cancellation_reason: null
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpointNullReason:
    """Pre-reason-key legacy cancellations: status="cancelled" but
    the reason key was never written. The handler must surface
    ``cancellation_reason: null`` (the JSON serialisation of Python
    ``None``), NOT raise 404 or 500. The ``null`` field is
    *reserved* for these legacy cancellations (handler docstring
    routes.py:705-709) — it is NOT the marker for "request exists
    but is not cancelled" (that case is a 400).
    """

    def test_cancelled_with_null_reason_returns_200(
        self, client: TestClient,
    ) -> None:
        """Status="cancelled", reason=None → 200 with
        ``cancellation_reason: null`` on the wire.

        Pins the on-the-wire shape that the v68 handler-direct
        test pins in Python-land (``result.cancellation_reason
        is None``). The TestClient layer is the only place where
        ``null`` (the JSON value) is observable; a regression
        that omitted the field on a null reason would surface
        here as a missing key.
        """
        with pytest.MonkeyPatch.context() as mp:
            _patch_redis_helpers(
                mp,
                status_value="cancelled",
                reason_value=None,
            )
            r = client.get("/v1/mods/req-legacy/cancellation_reason")

        assert r.status_code == 200, (
            f"expected 200 for legacy cancellation, got "
            f"{r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["status"] == "cancelled"
        # The null reason is in the envelope, not omitted. Pydantic
        # serialises Optional[str] = None as JSON null.
        assert "cancellation_reason" in body
        assert body["cancellation_reason"] is None
        assert body["request_id"] == "req-legacy"


# ---------------------------------------------------------------------------
# 404 — unknown request
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpoint404:
    """Unknown request_id → 404 with a FastAPI error envelope.

    The handler checks ``get_status`` first; if it returns ``None``,
    the handler raises ``HTTPException(404, detail=f"Request
    {request_id} not found")`` (routes.py:714-718). The reason
    lookup is NOT attempted — the handler short-circuits before
    reaching routes.py:732.
    """

    def test_unknown_request_returns_404(
        self, client: TestClient,
    ) -> None:
        """``get_status`` returns ``None`` → 404 with a FastAPI
        error envelope ``{"detail": "Request req-ghost not found"}``.

        The reason helper is NOT called. A regression that
        attempted the reason lookup before the status check
        would surface here as a 200 (the helper's mock would
        raise an AssertionError, but the handler's narrow
        transient-error catch would mask it as 200 with
        null — see ``test_transient_redis_failure_returns_200``
        for the same shape). Pin the "not called" invariant
        directly.
        """
        with pytest.MonkeyPatch.context() as mp:
            get_status, get_cancellation_reason = _patch_redis_helpers(
                mp,
                status_value=None,
                reason_value=None,
                expect_reason_call=False,
            )
            r = client.get("/v1/mods/req-ghost/cancellation_reason")

        assert r.status_code == 404, (
            f"expected 404 for unknown request, got "
            f"{r.status_code}: {r.text!r}"
        )
        # FastAPI error envelope shape — the handler raised
        # ``HTTPException(status_code=404, detail=...)`` and
        # FastAPI's default exception handler serialises it as
        # ``{"detail": <str>}``. The detail echoes the request_id
        # so the client can correlate.
        body = r.json()
        assert "detail" in body
        assert "req-ghost" in body["detail"]
        assert "not found" in body["detail"].lower()

        # Status helper was called (the handler checks it first).
        assert get_status.await_count == 1
        # Reason helper was NOT called — the handler short-circuits.
        assert get_cancellation_reason.await_count == 0

        # Cache-Control: no-store is still set — the middleware
        # fires on path match, not on status code. A regression
        # that gated the header on status_code == 200 would
        # surface here as the header being missing.
        assert r.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# 400 — request exists but is not cancelled
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpoint400:
    """Status must be exactly ``"cancelled"`` — every other value → 400.

    The docstring at routes.py:705-709 says "cancellation reason is
    meaningless for non-cancelled requests". Parametrize every
    non-cancelled status to pin the contract. The reason lookup is
    NOT attempted on this branch (the handler short-circuits at
    routes.py:719-726).
    """

    @pytest.mark.parametrize(
        "non_cancelled_status",
        ["running", "done", "failed", "pending", "error"],
    )
    def test_non_cancelled_status_returns_400(
        self,
        client: TestClient,
        non_cancelled_status: str,
    ) -> None:
        """Status is something other than ``"cancelled"`` → 400
        with a FastAPI error envelope ``{"detail": "Request
        <id> is not cancelled (current status: <status>)"}``.

        The detail echoes the current status so a client can
        tell "running" vs "done" vs "failed" without a
        follow-up call. The reason helper is NOT called.

        Parametrize the five most common non-cancelled statuses
        to pin that none accidentally falls through to the
        reason-lookup branch. (``unknown`` is intentionally
        excluded — the handler's only matching value is
        ``"cancelled"``, but ``"unknown"`` is sometimes used
        as a fallback in adjacent code paths. The test for
        ``"unknown"`` belongs at the get_pipeline_state
        helper level, not here.)
        """
        with pytest.MonkeyPatch.context() as mp:
            get_status, get_cancellation_reason = _patch_redis_helpers(
                mp,
                status_value=non_cancelled_status,
                reason_value=None,
                expect_reason_call=False,
            )
            r = client.get("/v1/mods/req-non-cancelled/cancellation_reason")

        assert r.status_code == 400, (
            f"expected 400 for status={non_cancelled_status!r}, "
            f"got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        # FastAPI error envelope shape — detail is a string
        # (not a list of Pydantic error dicts, because the
        # 400 is raised inside the handler, not at the
        # route-boundary validation step).
        assert "detail" in body
        # The detail echoes BOTH the request_id AND the
        # current status — the handler formats the message
        # with both (routes.py:722-725). Pinning the
        # status-echo means a dashboard can show
        # "request is still running" without a follow-up
        # status call.
        assert "req-non-cancelled" in body["detail"]
        assert non_cancelled_status in body["detail"]
        assert "not cancelled" in body["detail"]

        # Status helper was called once.
        assert get_status.await_count == 1
        # Reason helper was NOT called — the handler
        # short-circuits at the 400 branch BEFORE the
        # reason lookup.
        assert get_cancellation_reason.await_count == 0


# ---------------------------------------------------------------------------
# Transient Redis failure on the reason lookup — graceful 200 with null
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpointTransientFailure:
    """Transient Redis error on the reason lookup → 200 with
    ``cancellation_reason: null`` (narrow catch at routes.py:734).

    The handler's narrow catch (ConnectionError,
    asyncio.TimeoutError, RuntimeError — see routes.py:734) logs
    a WARNING and surfaces ``cancellation_reason=None`` rather
    than failing the request. This is the documented
    graceful-degradation contract: the status lookup succeeded,
    the caller knows the request is cancelled; the reason can
    be back-filled by a follow-up call once Redis recovers.

    The TestClient layer pins the **wire shape** of this
    fallback — a 200 with ``cancellation_reason: null`` is the
    observable contract. A regression that broadened the catch
    to swallow ``Exception`` (which would mask programming
    bugs like ``AttributeError`` as a transient outage) would
    surface here as a test that uses a non-narrow exception
    type still producing 200 — the test for the non-narrow
    type is in v68's handler-direct test, NOT here (the
    TestClient doesn't need to re-pin the same negative
    surface; it pins the positive "this exception type is
    caught" contract).
    """

    def test_transient_redis_failure_returns_200_with_null(
        self, client: TestClient,
    ) -> None:
        """``get_status`` returns ``"cancelled"``,
        ``get_cancellation_reason`` raises ``ConnectionError`` →
        200 with ``cancellation_reason: null`` on the wire.

        The status succeeded (request IS cancelled); the
        reason is unavailable for now. The handler's
        narrow catch swallows ``ConnectionError`` (a
        documented transient type per routes.py:734),
        and the wire shows the documented
        graceful-degradation contract. A regression
        that didn't catch the exception would surface
        here as a 500 (the un-awaited
        ``ConnectionError`` would propagate through
        FastAPI's default exception handler).
        """
        with pytest.MonkeyPatch.context() as mp:
            get_status = AsyncMock(return_value="cancelled")
            mp.setattr("storage.redis.get_status", get_status)
            # The reason helper raises a transient error.
            # ``side_effect`` raises synchronously when the
            # mock is awaited, so the handler's
            # ``reason = await get_cancellation_reason(...)``
            # raises ConnectionError inside the try block.
            get_cancellation_reason = AsyncMock(
                side_effect=ConnectionError("redis transient outage"),
            )
            mp.setattr(
                "storage.redis.get_cancellation_reason",
                get_cancellation_reason,
            )

            r = client.get("/v1/mods/req-transient/cancellation_reason")

        assert r.status_code == 200, (
            f"expected 200 for transient reason failure, got "
            f"{r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["status"] == "cancelled"
        # The reason is null on the wire — the catch's
        # ``reason: str | None = None`` initial value
        # (routes.py:731) is what gets serialised.
        assert body["cancellation_reason"] is None
        # request_id round-trips even on the failure path.
        assert body["request_id"] == "req-transient"

        # Both helpers were called — the handler does NOT
        # short-circuit on the reason-lookup failure.
        assert get_status.await_count == 1
        assert get_cancellation_reason.await_count == 1


# ---------------------------------------------------------------------------
# Path-order trap regression — the more-specific suffix wins
# ---------------------------------------------------------------------------

class TestGetCancellationReasonEndpointPathOrder:
    """Path-order trap regression: ``/v1/mods/{request_id}/
    cancellation_reason`` must route to this handler, not the
    generic ``/v1/mods/{request_id}`` handler.

    FastAPI's path matching is declaration-order sensitive for
    overlapping path patterns. The handler docstring at
    routes.py:681-685 calls out the precedent (the
    cancellation_reasons + stats + generators endpoints are
    all registered BEFORE the generic ``{request_id}`` route
    to avoid capture). This test pins the same invariant for
    the per-request cancellation_reason endpoint: an arbitrary
    request_id with the ``/cancellation_reason`` suffix must
    still reach the cancellation_reason handler.

    Note: a regression that swapped the registration order
    would surface here as a 422 (the generic
    ``/v1/mods/{request_id}`` route's Pydantic-validated
    path param would reject the trailing segment, OR the
    generic handler would 404 because the request_id
    "req-foo/cancellation_reason" doesn't exist in
    storage). The exact failure mode depends on the
    generic handler's signature — pinning that we get 200
    (not 422/404 from the wrong handler) is the load-bearing
    assertion.
    """

    def test_underscore_in_request_id_routes_to_cancellation_reason(
        self, client: TestClient,
    ) -> None:
        """``/v1/mods/req_with_underscores/cancellation_reason`` →
        200, NOT 422/404 from the generic ``{request_id}`` route.

        The request_id pattern is a free-form ``str`` (no
        path-validator constraints at routes.py:696), so
        underscores are valid. A regression that moved
        the cancellation_reason route AFTER the generic
        ``{request_id}`` route would surface here as a
        404 (the generic handler would treat
        ``"req_with_underscores/cancellation_reason"``
        as a single request_id and miss the storage
        lookup, raising 404 from inside the handler).
        """
        with pytest.MonkeyPatch.context() as mp:
            _patch_redis_helpers(
                mp,
                status_value="cancelled",
                reason_value="user_cancelled",
            )
            r = client.get(
                "/v1/mods/req_with_underscores/cancellation_reason"
            )

        # 200 means the cancellation_reason handler ran. A 404
        # would mean the generic ``{request_id}`` handler
        # captured the request and missed the storage lookup
        # (it would have looked up a request_id of
        # "req_with_underscores/cancellation_reason" and
        # gotten None back). A 422 would mean FastAPI's
        # path-param validator rejected the multi-segment
        # path.
        assert r.status_code == 200, (
            f"expected 200 (cancellation_reason handler ran), got "
            f"{r.status_code}: {r.text!r}. A non-200 here usually "
            f"means the cancellation_reason route was registered "
            f"AFTER /v1/mods/{{request_id}} and got captured by "
            f"the generic handler — see the path-order note at "
            f"routes.py:681-685."
        )
        body = r.json()
        # The request_id is the segment BEFORE the
        # /cancellation_reason suffix — the handler
        # forwards the path param verbatim, and FastAPI's
        # path-param extraction stops at the first
        # non-matching segment. A regression that
        # captured the full suffix as the request_id
        # would surface here as the request_id on the
        # wire being "req_with_underscores/cancellation_reason".
        assert body["request_id"] == "req_with_underscores"
        assert body["status"] == "cancelled"
        assert body["cancellation_reason"] == "user_cancelled"
