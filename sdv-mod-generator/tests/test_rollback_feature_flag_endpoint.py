"""HTTP-layer 200/404/409 contract tests for ``POST /v1/feature_flags/{name}/rollback``.

Closes the v153 "option (5)" gap from the five-option menu: the
rollback endpoint's TestClient coverage. v153 closed the toggle
endpoint (200/404/423). This file closes the rollback endpoint's
distinct surface (200/404/**409**). The 422 surface does not apply
here because the endpoint takes NO request body (preserved from
the source bundle, ``app/api/routes.py`` lines 1582-1587).

  - 200 — happy path. ``rollback_flag`` returns a dict; the route
    copies the five fields into ``FeatureFlagRollbackResponse``.
  - 404 — ``rollback_flag`` returns ``None`` AND ``name`` is not in
    ``_DEFAULT_FLAGS`` or ``_overrides``. The handler re-checks the
    registry to distinguish 404 from 409.
  - 409 — ``rollback_flag`` returns ``None`` AND ``name`` IS in the
    registry. The flag exists but has no rollbackable history.

Each test pins the FastAPI status-code mapping, the JSON body
shape (200 only), and the ``rollback_flag`` call args. The 409
case is the unique surface vs the toggle endpoint (409 vs the
toggle's 423 — two distinct 4xx codes for two distinct failure
modes).

The handler at ``app/api/routes.py`` lines 1564-1721 uses a
deferred import (``from orchestrator.feature_flags import
_DEFAULT_FLAGS, _overrides, rollback_flag`` inside the handler
body), so ``monkeypatch.setattr`` at the module attribute level
binds correctly at handler-invocation time. Same deferred-import
trick v152 + v153 used.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _StubbedDeps:
    """Mock helper for the rollback endpoint's three deps.

    The rollback handler has THREE downstream symbols:

      1. ``rollback_flag`` — the workhorse; returns a dict on
         success or ``None`` on failure (unknown flag OR no history).
      2. ``_DEFAULT_FLAGS`` — read-only defaults dict. The route
         re-checks ``name in _DEFAULT_FLAGS`` to distinguish 404
         from 409.
      3. ``_overrides`` — mutable live state dict. Same purpose
         as #2; either membership marks the flag as known.

    All three are looked up via the deferred import inside the
    handler body, so ``monkeypatch.setattr`` against the module
    attributes binds correctly at handler-invocation time.

    ``_DEFAULT_FLAGS`` is a real dict (not a mock) so
    ``__contains__`` is deterministic; tests seed it directly.
    ``_overrides`` is left as the module default (a MagicMock's
    ``__contains__`` returns False, which is the conservative
    default for the 200 and 404 cases).
    """

    def __init__(self) -> None:
        self.rollback_flag = MagicMock()
        self.default_flags: dict[str, bool] = {}

    def __enter__(self) -> "_StubbedDeps":
        self._mp = pytest.MonkeyPatch()
        self._mp.setattr(
            "orchestrator.feature_flags.rollback_flag",
            self.rollback_flag,
        )
        self._mp.setattr(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            self.default_flags,
        )
        return self

    def __exit__(self, *args: object) -> None:
        self._mp.undo()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestRollbackFeatureFlagEndpoint200:
    """Happy path — ``rollback_flag`` returns a fully-populated dict.

    Pins the full response shape so a future refactor that drops
    a field, renames a key, or changes a type surfaces here first.
    """

    def test_happy_path_returns_200_with_full_response(
        self, client: TestClient,
    ) -> None:
        """``rollback_flag`` returns the rollback descriptor; the
        handler copies all five fields into the response model.
        """
        with _StubbedDeps() as deps:
            deps.rollback_flag.return_value = {
                "name": "flag_a",
                "rolled_back_from": True,
                "rolled_back_to": False,
                "restored_entry_index": 0,
                "history_size_at_rollback": 1,
            }
            r = client.post("/v1/feature_flags/flag_a/rollback")
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body["name"] == "flag_a"
        assert body["rolled_back_from"] is True
        assert body["rolled_back_to"] is False
        assert body["restored_entry_index"] == 0
        assert body["history_size_at_rollback"] == 1
        # ``rollback_flag`` is called with exactly one positional
        # arg — the flag name from the path. No body to parse.
        deps.rollback_flag.assert_called_once_with("flag_a")


class TestRollbackFeatureFlagEndpoint404:
    """Unknown flag — ``rollback_flag`` returns ``None`` AND the
    flag is not in ``_DEFAULT_FLAGS`` or ``_overrides``.

    The handler re-checks the registry to map the ``None`` helper
    return to 404 (unknown) vs 409 (no history). A regression
    that drops the re-check would collapse both error cases into
    a single status, hiding the 404 from operators who typo'd a
    flag name.
    """

    def test_unknown_flag_returns_404_with_detail(
        self, client: TestClient,
    ) -> None:
        """``rollback_flag`` returns ``None``; ``_DEFAULT_FLAGS`` is
        empty so the registry re-check returns False. 404 with
        ``detail`` mentioning the flag name.
        """
        with _StubbedDeps() as deps:
            deps.rollback_flag.return_value = None
            r = client.post(
                "/v1/feature_flags/not_a_real_flag/rollback",
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
        deps.rollback_flag.assert_called_once_with("not_a_real_flag")


class TestRollbackFeatureFlagEndpoint409:
    """Known flag with no rollbackable history — ``rollback_flag``
    returns ``None`` AND the flag IS in ``_DEFAULT_FLAGS`` (or
    ``_overrides``).

    The 409 is intentional: the request was well-formed, the flag
    exists, but there is nothing to undo. 404 would be a lie (the
    flag is known); 422 would also be wrong (no body to validate).
    409 is the standard "the resource state prevents the operation"
    code.
    """

    def test_known_flag_with_no_history_returns_409_with_detail(
        self, client: TestClient,
    ) -> None:
        """``rollback_flag`` returns ``None``; ``_DEFAULT_FLAGS``
        contains the flag name so the registry re-check returns
        True. 409 with ``detail`` mentioning the flag name.
        """
        with _StubbedDeps() as deps:
            # Seed the defaults dict so the registry re-check
            # treats ``flag_a`` as known.
            deps.default_flags["flag_a"] = True
            deps.rollback_flag.return_value = None
            r = client.post("/v1/feature_flags/flag_a/rollback")
        assert r.status_code == 409, (
            f"expected 409, got {r.status_code}: {r.text!r}"
        )
        # Loose pin on the detail string — exact wording is an
        # implementation detail; assert only the flag name
        # appears so an operator dashboard can render the error
        # without parsing the whole 409.
        assert "flag_a" in r.json()["detail"]
        # ``rollback_flag`` was called even though no rollback
        # was possible — the handler uses the helper's ``None``
        # return as the signal, then re-checks the registry to
        # decide between 404 and 409.
        deps.rollback_flag.assert_called_once_with("flag_a")