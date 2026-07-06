"""Tests for the POST /v1/feature_flags/{name} endpoint (handler-direct).

Companion to the v132 ``test_get_feature_flags`` (read-the-now) and
v133 ``test_get_feature_flags_history`` (read-the-audit-log)
rounds: v132 + v133 pinned the read-only pair of Session 5, this
round pins the FIRST mutation endpoint (the toggle). The remaining
five handler-level orphans (``rollback``, ``pin``, ``unpin``,
``pin_state``, ``pins``) all mutate state through their own
helpers, but they share the same pattern: ``unittest.mock.patch``
on a single ``orchestrator.feature_flags`` function + a handler
call + an assertion on the response. v134 establishes that pattern
for the whole mutation family; v135+ should reuse it.

Mirrors the ``test_list_packs.py`` / ``test_get_feature_flags.py``
pattern: import the route handler, patch the source module it
reaches into (``orchestrator.feature_flags``), call the handler,
assert the response. No TestClient — the handler is short and
exercising it directly gives the test a single seam that
survives any future APIRouter / dep-injection reshuffle.

What is pinned here:

  1. Happy path — ``set_flag`` returns the previous ``bool`` value
     (e.g. ``True``) and the request body asked for the OPPOSITE
     (``False``). The handler builds a ``FeatureFlagChangeResponse``
     with ``previous_value=True`` (the captured return), ``enabled=
     False`` (the requested value), and ``name`` taken from the
     path (NOT the body's ``name`` field — the body has one too,
     but the handler trusts the path).
  2. No-op write — ``set_flag`` returns ``True`` AND the request
     body asked for ``True``. The handler still builds a 200
     response with ``previous_value=True`` (no-op is a success,
     not a 409 — the audit log captures it separately). This pins
     the contract that a toggle to the current value is a
     legitimate, auditable operation, not an error.
  3. Unknown flag — ``set_flag`` returns ``None`` (the master's
     deny-by-default contract for unregistered names). The
     handler raises ``HTTPException(status_code=404)`` with
     ``detail`` mentioning the bad flag name. This is the
     opposite of the history endpoint's "empty is fine" contract
     — a toggle against an unknown name fails closed.
  4. Pinned flag — ``set_flag`` raises ``FlagPinnedError``
     (because the flag is locked via ``pin_flag`` and the
     requested value drifts from the pinned value). The handler
     raises ``HTTPException(status_code=423)`` with ``detail``
     mentioning the pin and the current value. This is the v39
     addition over the branch source — the branch's cleanroom
     module has no pin-lock semantics, so its
     ``record_flag_change`` helper never raises. The 423 mapping
     is a deliberate "fail with a clear actionable message" so
     an operator dashboard can render "unpin first" without
     parsing 500-class responses.
  5. Schema integration — the handler's return value satisfies
     the :class:`FeatureFlagChangeResponse` Pydantic contract.
     Guards against a future handler refactor that drops a
     required field (``name``, ``enabled``, or
     ``previous_value``).

Not pinned (intentional, deferred):

  - HTTP-level tests (200/404/423 status codes, JSON content
    type, FastAPI's automatic 422 on malformed body) — those
    belong in a TestClient round. The handler-direct round is
    sufficient for the v134 seam; a TestClient round (if
    desired) is a small follow-up.
  - Body-vs-path ``name`` mismatch — the handler ignores the
    body's ``name`` field and uses the path parameter as the
    source of truth. The happy-path test uses ``name="flag_a"``
    in BOTH (so the response trivially matches), but does not
    explicitly assert that a divergent body's ``name`` is
    ignored. Pin that in a future TestClient round.
  - Logger info events (``api.feature_flag.updated``,
    ``api.feature_flag.update_unknown``,
    ``api.feature_flag.update_locked``) — structlog's own test
    suite pins that, and re-asserting here would couple the
    test to a specific log handler.
  - The exact ``detail`` string for the 423 case — pinned only
    loosely (the test checks for the flag name and the word
    "pinned" / "unpin" — substring match — because the exact
    wording is an implementation detail of the v39 addition).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.api.schemas import FeatureFlagChangeResponse, FeatureFlagUpdate
from orchestrator.feature_flags import FlagPinnedError


def _body(name: str, enabled: bool) -> FeatureFlagUpdate:
    """Build a request body mirroring what FastAPI would deserialize."""
    return FeatureFlagUpdate(name=name, enabled=enabled)


class TestUpdateFeatureFlagHandler:
    """``update_feature_flag`` handler-direct contract tests."""

    async def test_happy_path_returns_previous_and_new_value(self) -> None:
        """``set_flag`` returns ``True`` (the previous value); the
        request asked for ``False``. The handler returns a 200
        with ``previous_value=True``, ``enabled=False``,
        ``name="flag_a"`` (from the path)."""
        from app.api.routes import update_feature_flag

        with patch(
            "orchestrator.feature_flags.set_flag",
            return_value=True,
        ) as mock_set:
            result = await update_feature_flag(
                name="flag_a", body=_body("flag_a", False),
            )

        # The handler must pass the path name (not the body's name)
        # through to ``set_flag`` — pinning this catches a future
        # refactor that accidentally trusts the body's ``name``.
        mock_set.assert_called_once_with(name="flag_a", enabled=False)
        assert isinstance(result, FeatureFlagChangeResponse)
        assert result.name == "flag_a"
        assert result.enabled is False
        assert result.previous_value is True

    async def test_no_op_write_returns_success(self) -> None:
        """``set_flag`` returns ``True`` (the previous value) AND
        the request asked for ``True``. The handler returns 200
        with ``previous_value=enabled=True``. A toggle to the
        current value is a legitimate, auditable operation — the
        audit log captures the no-op and the operator dashboard
        surfaces it as a "you re-confirmed the current value"
        event, not as a 409."""
        from app.api.routes import update_feature_flag

        with patch(
            "orchestrator.feature_flags.set_flag",
            return_value=True,
        ):
            result = await update_feature_flag(
                name="flag_a", body=_body("flag_a", True),
            )

        assert isinstance(result, FeatureFlagChangeResponse)
        assert result.name == "flag_a"
        assert result.enabled is True
        assert result.previous_value is True

    async def test_unknown_flag_returns_404(self) -> None:
        """``set_flag`` returns ``None`` (master's deny-by-default
        contract for unregistered names). The handler raises
        ``HTTPException(status_code=404)`` with ``detail``
        mentioning the bad flag name. This is the OPPOSITE of
        the history endpoint's "empty is fine" contract — a
        toggle against an unknown name fails closed."""
        from fastapi import HTTPException

        from app.api.routes import update_feature_flag

        with patch(
            "orchestrator.feature_flags.set_flag",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_feature_flag(
                    name="not_a_real_flag",
                    body=_body("not_a_real_flag", True),
                )

        assert exc_info.value.status_code == 404
        assert "not_a_real_flag" in str(exc_info.value.detail)

    async def test_pinned_flag_returns_423(self) -> None:
        """``set_flag`` raises ``FlagPinnedError`` (because the
        flag is locked via ``pin_flag`` and the requested value
        drifts from the pinned value). The handler raises
        ``HTTPException(status_code=423)`` with ``detail``
        mentioning the pin. This is the v39 addition over the
        branch source."""
        from fastapi import HTTPException

        from app.api.routes import update_feature_flag

        pinned_error = FlagPinnedError(flag_name="flag_a", current_value=True)

        with patch(
            "orchestrator.feature_flags.set_flag",
            side_effect=pinned_error,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_feature_flag(
                    name="flag_a", body=_body("flag_a", False),
                )

        assert exc_info.value.status_code == 423
        # The exact wording is an implementation detail; pin the
        # contract that the detail mentions the flag name and
        # the pin (so an operator dashboard can render "unpin
        # first" without parsing the whole string).
        detail = str(exc_info.value.detail)
        assert "flag_a" in detail
        assert "pinned" in detail.lower() or "unpin" in detail.lower()


class TestUpdateFeatureFlagSchemaIntegration:
    """Schema-vs-handler integration test for the response shape."""

    def test_response_model_validates_handler_output(self) -> None:
        """The handler's return value satisfies the
        :class:`FeatureFlagChangeResponse` Pydantic contract —
        guards against a future handler refactor that drops a
        required field (``name``, ``enabled``, or
        ``previous_value``)."""
        from app.api.routes import update_feature_flag

        # Sync call site via ``asyncio.run`` to keep the file
        # dependency-free for this one method (same pattern as
        # v132's / v133's ``Test*SchemaIntegration``).
        with patch(
            "orchestrator.feature_flags.set_flag",
            return_value=False,
        ):
            result = asyncio.run(
                update_feature_flag(
                    name="flag_x", body=_body("flag_x", True),
                )
            )

        # Pydantic v2 round-trip — re-validate the already-built
        # model instance to confirm it conforms to the schema.
        revalidated = FeatureFlagChangeResponse.model_validate(
            result.model_dump()
        )
        assert revalidated == result
        assert revalidated.name == "flag_x"
        assert revalidated.enabled is True
        assert revalidated.previous_value is False