"""HTTP-layer 200/404 contract tests for ``GET /v1/feature_flags/{name}/pin``.

Closes the **seventh** of eight admin endpoints at the TestClient
layer. The schedule:

  - toggle v152 (422) + v153 (200/404/423)
  - rollback v154 (200/404/409)
  - pin v155 (200/404)
  - unpin v156 (200/404)
  - list v157 (200)
  - history v158 (200/422)
  - **pin_state v159 (200/404) — this file**
  - pins list (still to come — v160+)

The pin_state endpoint is the most interesting of the two remaining
GETs because it HAS a 404 surface (unknown flag name returns 404 —
the flag registry IS a lookup, unlike the v158 history endpoint
which was an audit-log query with a defensive-empty pattern). Three
sub-cases are pinned here:

  1. **Happy path: pinned=True, current_value=True** — both
     helpers return ``True``; the response carries all four
     fields (``name``, ``pinned``, ``current_value``, ``known``)
     with ``known=True`` hard-coded on 200.
  2. **Happy path: pinned=True, current_value=False** — pins
     the independence of the two helpers. A flag can be locked
     at any value (locked-on or locked-off), and a refactor
     that computed ``pinned`` from ``is_enabled`` would
     silently drop the lock state.
  3. **Happy path: pinned=False, current_value=True** — the
     common "mutable and on" query. Mutability is the
     default; the helper returns ``False`` for any flag not
     in ``_locked_pins``.
  4. **Override-only flag is known** — flag exists in
     ``_overrides`` but NOT in ``_DEFAULT_FLAGS``; handler
     must accept (UNKNOWN-CHECK is a UNION of the two
     registries). Mirrors the v138 override-only test.
  5. **404 for unknown flag** — name in NEITHER registry;
     handler raises ``HTTPException(404)`` with detail
     carrying the flag name. Mirrors v155 / v156 / v18.

No 422 surface: the endpoint takes NO request body and uses
NO ``Query`` validators. The ``{name}`` path parameter is a
plain ``str`` (no ``min_length`` / ``max_length`` / ``pattern``),
so FastAPI never rejects the request before dispatch. A
refactor that added length validation here would still be
fine for 200 / 404 contracts (it would just shift some 404s
to 422 for invalid syntax); the absence of 422 cases is a
load-bearing assertion about the route's *current* contract,
not a future-proofing prediction.

The handler at ``app/api/routes.py`` lines 1971-2087 uses a
body-level ``from orchestrator.feature_flags import (is_enabled,
is_pinned, _DEFAULT_FLAGS, _overrides)`` import — same
"import inside the handler body" pattern v155 + v156 + v158
use. ``monkeypatch.setattr`` on each module attribute binds
correctly at handler-invocation time:

  - ``is_pinned`` and ``is_enabled`` are sync functions with
    ``MagicMock(return_value=...)`` — not ``AsyncMock``,
    same as v158's ``get_history`` patch.
  - ``_DEFAULT_FLAGS`` and ``_overrides`` are rebindable
    module-level dicts; ``monkeypatch.setattr(..., {...
    : True})`` swaps them for the duration of the test and
    restores them after.

In the 404 case, the handler raises BEFORE invoking either
helper, so neither ``is_pinned`` nor ``is_enabled`` is
called. ``assert_not_called()`` on both pins the "early
404 short-circuit" contract: a refactor that moved the
unknown-flag check after the helper calls would still
return 404 but would have called the helpers first;
``assert_not_called`` catches that.

In the override-only case (case 4), the helper
``is_pinned`` / ``is_enabled`` IS called (because the
unknown-flag check passes), but the flag is registered
in ``_overrides`` not ``_DEFAULT_FLAGS``. The ``name in
_DEFAULT_FLAGS or name in _overrides`` union is the
load-bearing detail: a refactor that switched to
``known_flags()`` (which only returns defaults) would
404 here on master but 200 on the branch — the wider
union is intentional, mirrors v138, and is pinned by
case 4's "name in overrides only" setup.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestPinStateFeatureFlagEndpoint200:
    """Happy-path 200 contract tests for ``GET /v1/feature_flags/{name}/pin``."""

    def test_happy_path_pinned_and_enabled_returns_full_response(
        self, client: TestClient,
    ) -> None:
        """Both ``is_pinned`` and ``is_enabled`` return ``True``.
        Response carries all four fields, ``known=True``
        hard-coded on 200, content-type is ``application/json``,
        and both helpers are called with the URL-path ``name``
        as their sole positional argument.

        Pins the FastAPI status-code mapping (200), the JSON
        content-type (dashboards key off this header), the wire
        shape of every field (``name`` / ``pinned`` /
        ``current_value`` / ``known``), and the handler's
        forwarding of the two helpers' return values verbatim
        into the response model.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "orchestrator.feature_flags._DEFAULT_FLAGS",
                {"flag_a": True},
            )
            mp.setattr(
                "orchestrator.feature_flags._overrides",
                {},
            )
            is_pinned = MagicMock(return_value=True)
            is_enabled = MagicMock(return_value=True)
            mp.setattr("orchestrator.feature_flags.is_pinned", is_pinned)
            mp.setattr("orchestrator.feature_flags.is_enabled", is_enabled)

            r = client.get("/v1/feature_flags/flag_a/pin")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert body == {
            "name": "flag_a",
            "pinned": True,
            "current_value": True,
            "known": True,
        }
        # Both helpers must have been called exactly once with
        # the URL-path ``name`` as their sole positional arg.
        # A refactor that called ``is_pinned()`` with no args
        # (defaulting to a global "any flag" check) would
        # silently break this pin.
        is_pinned.assert_called_once_with("flag_a")
        is_enabled.assert_called_once_with("flag_a")

    def test_pinned_true_current_value_false(
        self, client: TestClient,
    ) -> None:
        """A flag that is locked (``pinned=True``) AND currently
        off (``current_value=False``). Pins the independence of
        the two helpers: a flag can be locked at ANY value, not
        only at its current value.

        Without this pin, a future refactor that computed
        ``pinned`` from ``is_enabled`` would silently drop the
        lock state for any flag locked at ``False`` — a real
        operational scenario where an operator deliberately
        locks a flag in its off-state to keep a feature
        suppressed.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "orchestrator.feature_flags._DEFAULT_FLAGS",
                {"flag_a": False},
            )
            mp.setattr(
                "orchestrator.feature_flags._overrides",
                {},
            )
            is_pinned = MagicMock(return_value=True)
            is_enabled = MagicMock(return_value=False)
            mp.setattr("orchestrator.feature_flags.is_pinned", is_pinned)
            mp.setattr("orchestrator.feature_flags.is_enabled", is_enabled)

            r = client.get("/v1/feature_flags/flag_a/pin")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body == {
            "name": "flag_a",
            "pinned": True,
            "current_value": False,
            "known": True,
        }
        # Both helpers called with ``flag_a``; ``is_enabled``
        # returning ``False`` must NOT cause the handler to
        # report ``pinned=False`` (independence pin).
        is_pinned.assert_called_once_with("flag_a")
        is_enabled.assert_called_once_with("flag_a")

    def test_not_pinned_default_enabled(
        self, client: TestClient,
    ) -> None:
        """``pinned=False`` and ``current_value=True``. The
        common "mutable and on" query — a flag at its default
        value with no pin lock.

        Mirrors v156's no-op unpin (which sets
        ``was_pinned=False``) but on the read-only GET side:
        a flag that has never been pinned returns
        ``pinned=False`` regardless of its value.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "orchestrator.feature_flags._DEFAULT_FLAGS",
                {"flag_a": True},
            )
            mp.setattr(
                "orchestrator.feature_flags._overrides",
                {},
            )
            is_pinned = MagicMock(return_value=False)
            is_enabled = MagicMock(return_value=True)
            mp.setattr("orchestrator.feature_flags.is_pinned", is_pinned)
            mp.setattr("orchestrator.feature_flags.is_enabled", is_enabled)

            r = client.get("/v1/feature_flags/flag_a/pin")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body == {
            "name": "flag_a",
            "pinned": False,
            "current_value": True,
            "known": True,
        }
        is_pinned.assert_called_once_with("flag_a")
        is_enabled.assert_called_once_with("flag_a")

    def test_override_only_flag_is_known(
        self, client: TestClient,
    ) -> None:
        """Flag exists in ``_overrides`` but NOT in
        ``_DEFAULT_FLAGS``. The UNKNOWN-CHECK is a UNION of the
        two registries (``name in _DEFAULT_FLAGS or name in
        _overrides``), so an override-only flag must be
        accepted and the helpers must be called.

        Without this pin, a refactor that switched the unknown
        check to ``known_flags()`` (which only returns the
        ``_DEFAULT_FLAGS`` keys — confirmed at
        ``orchestrator/feature_flags.py`` line 10's
        ``registry-inspection helpers ``known_flags`` /``
        docstring) would 404 here on master but 200 on the
        branch. The wider union is intentional: it matches
        v138's override-only pin_state case and the v41/v42
        pin/unpin handlers, and it lets operators register
        new flags at runtime via overrides without first
        adding them to the defaults.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "orchestrator.feature_flags._DEFAULT_FLAGS",
                {},
            )
            mp.setattr(
                "orchestrator.feature_flags._overrides",
                {"runtime_flag": True},
            )
            is_pinned = MagicMock(return_value=False)
            is_enabled = MagicMock(return_value=True)
            mp.setattr("orchestrator.feature_flags.is_pinned", is_pinned)
            mp.setattr("orchestrator.feature_flags.is_enabled", is_enabled)

            r = client.get("/v1/feature_flags/runtime_flag/pin")

        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text!r}"
        )
        body = r.json()
        assert body == {
            "name": "runtime_flag",
            "pinned": False,
            "current_value": True,
            "known": True,
        }
        is_pinned.assert_called_once_with("runtime_flag")
        is_enabled.assert_called_once_with("runtime_flag")


class TestPinStateFeatureFlagEndpoint404:
    """404 contract for ``GET /v1/feature_flags/{name}/pin``.

    The unknown-flag check (``name not in _DEFAULT_FLAGS and
    name not in _overrides``) runs BEFORE either helper, so:

      - ``is_pinned.assert_not_called()`` pins the early-
        short-circuit contract: a refactor that moved the
        helper calls ahead of the unknown-flag check would
        still return 404 but would have invoked the helpers
        first; the assert-not-called pin catches that.
      - ``is_enabled.assert_not_called()`` does the same
        for the second helper.
      - The 404 ``detail`` substring carries the flag name
        verbatim (formatted via ``f"Unknown feature flag:
        {name!r}"`` at ``app/api/routes.py`` line 2070),
        which produces ``Unknown feature flag: 'not_a_real_flag'``
        including the single quotes around the name. Pin
        just the bare flag-name substring (no quote
        characters) so the test stays valid if the quoting
        style changes but breaks if the name is dropped
        entirely.
    """

    def test_unknown_flag_returns_404_with_name_in_detail(
        self, client: TestClient,
    ) -> None:
        """``not_a_real_flag`` is in NEITHER ``_DEFAULT_FLAGS``
        NOR ``_overrides``; handler raises ``HTTPException(404)``
        with detail carrying the flag name verbatim.

        Pins (a) status-code mapping (404), (b) neither helper
        is called (early short-circuit), and (c) the detail
        message references the requested name so operators
        can spot typos.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "orchestrator.feature_flags._DEFAULT_FLAGS",
                {},
            )
            mp.setattr(
                "orchestrator.feature_flags._overrides",
                {},
            )
            is_pinned = MagicMock()
            is_enabled = MagicMock()
            mp.setattr("orchestrator.feature_flags.is_pinned", is_pinned)
            mp.setattr("orchestrator.feature_flags.is_enabled", is_enabled)

            r = client.get("/v1/feature_flags/not_a_real_flag/pin")

        assert r.status_code == 404, (
            f"expected 404, got {r.status_code}: {r.text!r}"
        )
        # Neither helper ran — the unknown-flag check fired
        # first and raised before reaching them. A regression
        # that moved the helper calls ahead of the check
        # would still return 404 but would call the helpers
        # first; the assert-not-called pins catch that.
        is_pinned.assert_not_called()
        is_enabled.assert_not_called()
        body = r.json()
        assert "detail" in body
        # Pin the bare flag-name substring (no quotes) so the
        # test stays valid if the quoting style changes but
        # breaks if the name is dropped from the message
        # entirely (the load-bearing operator-typo
        # diagnosability).
        assert "not_a_real_flag" in str(body["detail"])