"""HTTP-layer 200/404 contract tests for ``POST /v1/feature_flags/{name}/unpin``.

Mirror of v155's ``test_pin_feature_flag_endpoint.py`` for the
sibling ``unpin_feature_flag`` endpoint (route handler at
``app/api/routes.py`` lines 1846-1968, helper
``orchestrator.feature_flags.unpin_flag`` at
``orchestrator/feature_flags.py`` lines 287-315). Closes the
**fourth** of eight admin endpoints at the TestClient layer
(toggle v152/v153, rollback v154, pin v155, unpin v156).

The unpin endpoint is the inverse of v155's pin endpoint with
one load-bearing difference: ``unpin_flag`` populates
``was_pinned`` from the actual locked-set membership, while
``pin_flag`` returns ``already_pinned`` for the same role. The
handler then hard-codes the OPPOSITE field to ``False``:

  - Pin endpoint  → ``already_pinned`` from helper, ``was_pinned=False`` (hard-coded).
  - Unpin endpoint → ``was_pinned`` from helper, ``already_pinned=False` (hard-coded).

The shared ``FeatureFlagPinResponse`` schema carries both
fields so the two endpoints have byte-identical wire shapes,
but only one of the two fields is ever observably ``True`` on
each endpoint. That asymmetry is the load-bearing test surface
for v156.

Two sub-cases pinned for 200:

  - **Real unpin**: ``was_pinned=True``. The flag was in
    ``_locked_pins`` and the call removed the lock.
  - **No-op un-unpin**: ``was_pinned=False``. The flag was
    already unlocked and the call was a no-op; the handler
    still returns 200 (NOT 4xx). Idempotent contract — the
    same monotonic shape as v155's pin endpoint, but with the
    ``was_pinned`` field carrying the role instead of
    ``already_pinned``.

And one sub-case for 404:

  - **Unknown flag**: ``unpin_flag`` returns ``None``. The
    handler raises ``HTTPException(404)`` with ``detail``
    mentioning the flag name.

The unpin endpoint has NO 422 surface (no request body, same
design as v155's pin endpoint) and NO 409 surface (unpin is
monotonic downward, just like pin is monotonic upward — neither
endpoint has a "conflict" outcome).

Each test pins the FastAPI status-code mapping, the JSON body
shape (200 only), and the ``unpin_flag`` call args. 404 uses a
loose ``detail`` substring pin (flag name only).

The handler uses a deferred import (``from orchestrator.feature_flags
import unpin_flag`` inside the handler body, ``app/api/routes.py``
line 1927), so ``monkeypatch.setattr`` at the module attribute level
binds correctly at handler-invocation time. Same deferred-import
trick v152 + v153 + v154 + v155 used.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestUnpinFeatureFlagEndpoint200:
    """Happy path — ``unpin_flag`` returns a fully-populated dict.

    Pins the full response shape so a future refactor that drops
    a field, renames a key, or changes a type surfaces here first.

    Two sub-cases are pinned:

      - **Real unpin**: ``was_pinned=True``. The helper just
        transitioned the flag from locked to unlocked. This is
        the load-bearing difference from v155: the unpin
        endpoint reads ``was_pinned`` from the helper's return,
        while v155's pin endpoint hard-coded it to ``False``.
      - **No-op un-unpin**: ``was_pinned=False``. The flag was
        already unlocked and the call is a no-op; the handler
        still returns 200 (NOT 4xx). Idempotent contract.
    """

    def test_real_unpin_returns_200_with_was_pinned_true(
        self, client: TestClient,
    ) -> None:
        """``unpin_flag`` returns the real-unpin descriptor
        (``was_pinned=True``); the handler copies all four
        helper fields into the response model and hard-codes
        ``already_pinned=False``.

        This is the inverse of v155's ``test_fresh_pin_returns_200_with_full_response``:
        pin hard-codes ``was_pinned``, unpin hard-codes
        ``already_pinned``. Without this pin, a future refactor
        that swaps the two fields (e.g. reads ``was_pinned``
        on the pin path or ``already_pinned`` on the unpin
        path) would silently desync the wire shape from the
        operator dashboard's expectations.
        """
        with pytest.MonkeyPatch.context() as mp:
            unpin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.unpin_flag",
                unpin_flag,
            )
            unpin_flag.return_value = {
                "name": "flag_a",
                "pinned": False,
                "was_pinned": True,
                "current_value": True,
            }
            r = client.post("/v1/feature_flags/flag_a/unpin")
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["pinned"] is False
        # ``was_pinned`` is read from the helper on the unpin
        # endpoint — NOT hard-coded like v155's ``was_pinned``.
        # A regression that hard-coded ``was_pinned`` to
        # ``False`` on the unpin side would surface here as
        # ``was_pinned is False`` instead of ``True``.
        assert body["was_pinned"] is True
        # ``already_pinned`` is hard-coded to False on the
        # unpin endpoint (only the pin helper sets it). A
        # regression that read ``already_pinned`` from the
        # unpin helper's return (which never sets it) would
        # surface as ``already_pinned=None`` in JSON, failing
        # the type assertion below.
        assert body["already_pinned"] is False
        assert body["current_value"] is True
        # ``unpin_flag`` is called with exactly one positional
        # arg — the flag name from the path. No body to parse.
        unpin_flag.assert_called_once_with("flag_a")

    def test_noop_ununpin_returns_200_with_was_pinned_false(
        self, client: TestClient,
    ) -> None:
        """``unpin_flag`` returns the no-op descriptor
        (``was_pinned=False``); the handler still returns
        200, NOT 409. Idempotent contract — un-unpinning an
        already-unpinned flag is a legitimate operator pattern
        (e.g. a dashboard that renders "lock removed" without
        branching on the response shape).

        Without this pin, a future refactor that maps
        ``was_pinned=False`` to a 409 would silently break
        the API contract and force every dashboard to read
        both the status code and the body field to decide
        whether to render the success view.
        """
        with pytest.MonkeyPatch.context() as mp:
            unpin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.unpin_flag",
                unpin_flag,
            )
            unpin_flag.return_value = {
                "name": "flag_a",
                "pinned": False,
                "was_pinned": False,
                "current_value": False,
            }
            r = client.post("/v1/feature_flags/flag_a/unpin")
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["pinned"] is False
        # ``was_pinned`` propagates the no-op signal all the
        # way through the handler. The helper set it to False,
        # the handler reads it, the response surfaces it.
        assert body["was_pinned"] is False
        assert body["already_pinned"] is False
        assert body["current_value"] is False
        unpin_flag.assert_called_once_with("flag_a")


class TestUnpinFeatureFlagEndpoint404:
    """Unknown flag — ``unpin_flag`` returns ``None``.

    The handler re-raises this as ``HTTPException(404)`` so a
    typo in the path fails closed (mirrors v16/v18/v40/v41/
    v155's sibling contracts).

    There is no 409 surface on the unpin endpoint. Un-unpinning
    an already-unpinned flag is a 200 no-op, not a 409
    conflict — unpin is monotonic downward, just as pin is
    monotonic upward. The 409 surface belongs only to v154's
    rollback endpoint.
    """

    def test_unknown_flag_returns_404_with_detail(
        self, client: TestClient,
    ) -> None:
        """``unpin_flag`` returns ``None``; the handler raises
        ``HTTPException(404)`` with ``detail`` mentioning the
        flag name.
        """
        with pytest.MonkeyPatch.context() as mp:
            unpin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.unpin_flag",
                unpin_flag,
            )
            unpin_flag.return_value = None
            r = client.post(
                "/v1/feature_flags/not_a_real_flag/unpin",
            )
        assert r.status_code == 404, (
            f"expected 404, got {r.status_code}: {r.text!r}"
        )
        # ``HTTPException.detail`` is serialized as the JSON
        # ``detail`` field. Exact wording is an implementation
        # detail; assert only the flag name appears so an
        # operator dashboard can render the error without
        # parsing the whole 404.
        assert "not_a_real_flag" in r.json()["detail"]
        # ``unpin_flag`` was called even though the flag was
        # unknown — the handler uses the helper's ``None``
        # return as the 404 signal. A regression that
        # short-circuited before calling ``unpin_flag`` would
        # surface as ``unpin_flag.call_count == 0`` below.
        unpin_flag.assert_called_once_with("not_a_real_flag")