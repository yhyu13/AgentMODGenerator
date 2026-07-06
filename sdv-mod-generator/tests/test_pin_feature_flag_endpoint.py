"""HTTP-layer 200/404 contract tests for ``POST /v1/feature_flags/{name}/pin``.

Closes the v153 / v154 "option (4)" gap from the five-option menu:
the pin endpoint's TestClient coverage. v152 + v153 covered the
toggle endpoint (422 + 200/404/423). v154 covered the rollback
endpoint (200/404/409). This file covers the pin endpoint
(200/404) at the FastAPI TestClient layer.

  - 200 — happy path. ``pin_flag`` returns a dict with four keys
    (``name``, ``pinned``, ``already_pinned``, ``current_value``);
    the handler copies the four fields into ``FeatureFlagPinResponse``
    and hard-codes ``was_pinned=False`` (``pin_flag`` never sets
    ``was_pinned`` — owned by ``unpin``).
  - 404 — ``pin_flag`` returns ``None``. The handler raises
    ``HTTPException(404)``.

The pin endpoint has NO 422 surface because it takes NO request
body (preserved from the source bundle design, ``app/api/routes.py``
lines 1739-1742). It has NO 409 surface because the unknown-flag
and already-pinned-no-op cases both flow through ``pin_flag``'s
return value — the helper does not distinguish them via status
code (it uses the ``already_pinned`` boolean). Already-pinned
re-pins are NOT an error: the handler returns 200 with
``already_pinned=True`` (idempotent contract). This is the
load-bearing difference from v154's rollback endpoint, where a
known flag with no rollbackable history returns 409.

Each test pins the FastAPI status-code mapping, the JSON body
shape (200 only), and the ``pin_flag`` call args. 404 uses a
loose ``detail`` substring pin (flag name only).

The handler at ``app/api/routes.py`` lines 1724-1843 uses a
deferred import (``from orchestrator.feature_flags import
pin_flag`` inside the handler body), so ``monkeypatch.setattr``
at the module attribute level binds correctly at
handler-invocation time. Same deferred-import trick v152 +
v153 + v154 used.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestPinFeatureFlagEndpoint200:
    """Happy path — ``pin_flag`` returns a fully-populated dict.

    Pins the full response shape so a future refactor that drops
    a field, renames a key, or changes a type surfaces here first.

    Two sub-cases are pinned:

      - **Fresh pin**: ``already_pinned=False``. The helper just
        transitioned the flag from unlocked to locked.
      - **No-op re-pin**: ``already_pinned=True``. The flag was
        already locked and the call is a no-op; the handler
        still returns 200 (NOT 4xx). Idempotent contract — the
        load-bearing difference from v154's rollback endpoint.
    """

    def test_fresh_pin_returns_200_with_full_response(
        self, client: TestClient,
    ) -> None:
        """``pin_flag`` returns the fresh-pin descriptor
        (``already_pinned=False``); the handler copies all four
        fields into the response model and hard-codes
        ``was_pinned=False``.
        """
        with pytest.MonkeyPatch.context() as mp:
            pin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.pin_flag",
                pin_flag,
            )
            pin_flag.return_value = {
                "name": "flag_a",
                "pinned": True,
                "already_pinned": False,
                "current_value": True,
            }
            r = client.post("/v1/feature_flags/flag_a/pin")
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["pinned"] is True
        assert body["already_pinned"] is False
        assert body["was_pinned"] is False
        assert body["current_value"] is True
        # ``pin_flag`` is called with exactly one positional
        # arg — the flag name from the path. No body to parse.
        pin_flag.assert_called_once_with("flag_a")

    def test_repin_returns_200_with_already_pinned(
        self, client: TestClient,
    ) -> None:
        """``pin_flag`` returns the no-op descriptor
        (``already_pinned=True``); the handler still returns
        200, NOT 409. Idempotent contract — pinning a locked
        flag is a legitimate operator pattern.

        Without this pin, a future refactor that maps
        ``already_pinned=True`` to a 409 would silently break
        the API contract.
        """
        with pytest.MonkeyPatch.context() as mp:
            pin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.pin_flag",
                pin_flag,
            )
            pin_flag.return_value = {
                "name": "flag_a",
                "pinned": True,
                "already_pinned": True,
                "current_value": False,
            }
            r = client.post("/v1/feature_flags/flag_a/pin")
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["pinned"] is True
        assert body["already_pinned"] is True
        # ``was_pinned`` is still hard-coded to False on the
        # pin endpoint, regardless of the helper's return. A
        # regression that read ``was_pinned`` from the helper
        # (which never sets it on the pin path) would surface
        # as ``was_pinned=None`` in JSON, which would fail
        # the type assertion below.
        assert body["was_pinned"] is False
        assert body["current_value"] is False
        pin_flag.assert_called_once_with("flag_a")


class TestPinFeatureFlagEndpoint404:
    """Unknown flag — ``pin_flag`` returns ``None``.

    The handler re-raises this as ``HTTPException(404)`` so a
    typo in the path fails closed (mirrors v16/v18/v40/v41's
    sibling contracts).

    There is no 409 surface on the pin endpoint (unlike
    v154's rollback endpoint). Re-pinning a locked flag is a
    200 no-op, not a 409 conflict — pinning is monotonic,
    rollback is not.
    """

    def test_unknown_flag_returns_404_with_detail(
        self, client: TestClient,
    ) -> None:
        """``pin_flag`` returns ``None``; the handler raises
        ``HTTPException(404)`` with ``detail`` mentioning the
        flag name.
        """
        with pytest.MonkeyPatch.context() as mp:
            pin_flag = MagicMock()
            mp.setattr(
                "orchestrator.feature_flags.pin_flag",
                pin_flag,
            )
            pin_flag.return_value = None
            r = client.post(
                "/v1/feature_flags/not_a_real_flag/pin",
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
        # ``pin_flag`` was called even though the flag was
        # unknown — the handler uses the helper's ``None``
        # return as the 404 signal. A regression that
        # short-circuited before calling ``pin_flag`` would
        # surface as ``pin_flag.call_count == 0`` below.
        pin_flag.assert_called_once_with("not_a_real_flag")