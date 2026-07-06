"""HTTP-layer 200/404/423 contract tests for ``POST /v1/feature_flags/{name}``.

Closes v152's option (2). v152 covered the **422** FastAPI surface
(Pydantic body validation); v134 covered the handler-direct 200/404/423
contract. This file closes the remaining gap: TestClient end-to-end
coverage of the 200/404/423 paths through FastAPI's request lifecycle
(routing → body parsing → handler invocation → HTTP status mapping →
response serialization). Together the three rounds pin the FULL surface
of the toggle endpoint:

  - v134 — handler-direct 200/404/423 (handler output shape, raised
    ``HTTPException``, single-mock side-effect pin).
  - v152 — TestClient 422 (Pydantic body validation, ``detail`` list
    shape, multi-prefix tolerance, zero side-effect pin).
  - v153 (this file) — TestClient 200/404/423 (FastAPI status-code
    mapping, JSON content type, response body deserialization).

Pinned: 200 happy path (handler returns ``FeatureFlagChangeResponse``),
200 no-op (audit-log entry, not a 409), 404 unknown (FastAPI maps the
raised ``HTTPException(404)`` to a 404 with ``detail`` mentioning the
flag), 423 pinned (v39 pin-lock — FastAPI maps the raised
``HTTPException(423)`` to a 423 with ``detail`` mentioning the flag
and the pin).

Not pinned (deferred): HTTP 422 (v152); structlog events; exact 423
``detail`` wording (loose pin — substring on flag name + "pinned"/
"unpin", same as v134); body-vs-path ``name`` mismatch (v134 pins
that at the call site).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from orchestrator.feature_flags import FlagPinnedError


class _StubbedDeps:
    """Single-mock helper — the toggle endpoint has exactly one
    downstream side effect (``orchestrator.feature_flags.set_flag``).

    The handler's ``from orchestrator.feature_flags import … set_flag``
    (line 1514 of ``app/api/routes.py``) is a deferred import that
    binds the module attribute at handler-invocation time, so patching
    ``orchestrator.feature_flags.set_flag`` via MonkeyPatch is enough.
    Same deferred-import trick v152 used for its 422 round.
    """

    def __init__(self) -> None:
        self.set_flag = MagicMock()

    def __enter__(self) -> "_StubbedDeps":
        self._mp = pytest.MonkeyPatch()
        self._mp.setattr(
            "orchestrator.feature_flags.set_flag", self.set_flag,
        )
        return self

    def __exit__(self, *args: object) -> None:
        self._mp.undo()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestUpdateFeatureFlagEndpoint200:
    """Happy path — ``set_flag`` returns the previous ``bool`` value.

    Confirms the FastAPI ``response_model`` is wired in (a regression
    that drops it would still return 200 by accident; the explicit
    ``response_model`` is what makes the contract enforceable).
    """

    def test_happy_path_returns_200_with_previous_value(
        self, client: TestClient,
    ) -> None:
        """``set_flag`` returns ``True``; request asks for ``False``.
        200 with ``previous_value=True``, ``enabled=False``,
        ``name="flag_a"``."""
        with _StubbedDeps() as deps:
            deps.set_flag.return_value = True
            r = client.post(
                "/v1/feature_flags/flag_a",
                json={"name": "flag_a", "enabled": False},
            )
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["enabled"] is False
        assert body["previous_value"] is True
        # Path parameter is source of truth — ``set_flag`` must be
        # called with the path name, not the body's ``name``.
        deps.set_flag.assert_called_once_with(
            name="flag_a", enabled=False,
        )

    def test_no_op_write_returns_200(self, client: TestClient) -> None:
        """``set_flag`` returns ``True``; request asks for ``True``.
        200 with ``previous_value=True``, ``enabled=True`` — a
        no-op is a legitimate, auditable operation, not a 409."""
        with _StubbedDeps() as deps:
            deps.set_flag.return_value = True
            r = client.post(
                "/v1/feature_flags/flag_a",
                json={"name": "flag_a", "enabled": True},
            )
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["enabled"] is True
        assert body["previous_value"] is True
        deps.set_flag.assert_called_once_with(
            name="flag_a", enabled=True,
        )


class TestUpdateFeatureFlagEndpoint404:
    """Unknown flag name — ``set_flag`` returns ``None``.

    The handler raises ``HTTPException(404)``; FastAPI maps the
    raised exception to a 404 response with a ``detail`` string.
    A regression that swallows the ``HTTPException`` (broad
    ``except Exception``) would surface as a 500 — this catches it.
    """

    def test_unknown_flag_returns_404_with_detail(
        self, client: TestClient,
    ) -> None:
        """``set_flag`` returns ``None`` (master's deny-by-default
        contract). 404 with ``detail`` mentioning the flag name.
        Mirror of v134's handler-direct 404 test, at TestClient."""
        with _StubbedDeps() as deps:
            deps.set_flag.return_value = None
            r = client.post(
                "/v1/feature_flags/not_a_real_flag",
                json={"name": "not_a_real_flag", "enabled": True},
            )
        assert r.status_code == 404, (
            f"expected 404, got {r.status_code}: {r.text!r}"
        )
        # ``HTTPException.detail`` is serialized as the JSON
        # ``detail`` field. Exact wording is an implementation
        # detail; we assert only the flag name appears so an
        # operator dashboard can render the error without parsing
        # the whole 404.
        assert "not_a_real_flag" in r.json()["detail"]
        deps.set_flag.assert_called_once_with(
            name="not_a_real_flag", enabled=True,
        )


class TestUpdateFeatureFlagEndpoint423:
    """Pinned flag — ``set_flag`` raises ``FlagPinnedError``.

    The v39 pin-lock contract. The handler catches the
    ``FlagPinnedError`` and re-raises it as ``HTTPException(423)``;
    FastAPI maps the raised exception to a 423 response. A
    regression that drops the ``try/except`` would let the original
    ``FlagPinnedError`` propagate to FastAPI's default 500 handler.
    """

    def test_pinned_flag_returns_423_with_detail(
        self, client: TestClient,
    ) -> None:
        """``set_flag`` raises ``FlagPinnedError`` (flag is locked
        via ``pin_flag`` and the requested value drifts from the
        pinned value). 423 with ``detail`` mentioning the flag and
        the pin."""
        pinned_error = FlagPinnedError(
            flag_name="flag_a", current_value=True,
        )
        with _StubbedDeps() as deps:
            deps.set_flag.side_effect = pinned_error
            r = client.post(
                "/v1/feature_flags/flag_a",
                json={"name": "flag_a", "enabled": False},
            )
        assert r.status_code == 423, (
            f"expected 423, got {r.status_code}: {r.text!r}"
        )
        # Same loose-pin pattern as v134's handler-direct 423 test.
        # Exact wording is an implementation detail of the v39
        # addition; assert only that flag name AND pin appear.
        detail = r.json()["detail"]
        assert "flag_a" in detail
        assert "pinned" in detail.lower() or "unpin" in detail.lower()
        deps.set_flag.assert_called_once_with(
            name="flag_a", enabled=False,
        )
