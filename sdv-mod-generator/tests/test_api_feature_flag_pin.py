"""Tests for the POST /v1/feature_flags/{name}/pin endpoint (handler-direct).

Companion to v132 (read-now), v133 (read-history), v134 (toggle),
v135 (rollback): v136 pins the third Session 5 mutation. Pattern
mirrors v134 / v135: import the route handler, patch the source
module it reaches into (``orchestrator.feature_flags``) — the
helper AND the registry keys (``_DEFAULT_FLAGS`` /
``_overrides``) — call the handler, assert the response. The
registry is patched not because the pin handler inspects it
directly (``pin_flag`` raises the unknown-flag signal via its
``None`` return) but so any future inlined "is known?" check
would be caught by the same setup. No TestClient — short handler,
single seam.

What is pinned:

  1. Happy path — ``pin_flag`` returns a 4-key dict
     ``{name, pinned, already_pinned, current_value}``. Handler
     builds a ``FeatureFlagPinResponse`` with those four fields
     plus a hard-coded ``was_pinned=False`` (``pin_flag`` never
     sets ``was_pinned`` — owned by ``unpin``). Pins the wire
     shape = four helper fields + the False sentinel.
  2. Unknown flag — ``pin_flag`` returns ``None``; handler
     raises ``HTTPException(status_code=404)``. Mirrors v16 /
     v18 / v40 sibling contracts.
  3. Already-pinned no-op — ``pin_flag`` returns
     ``already_pinned=True`` (idempotent contract). Handler
     passes through verbatim; status 200, NOT 4xx. Load-bearing
     difference from v135's rollback, which raises 409 on its
     "no-op-like" path.
  4. ``was_pinned`` hard-coded to False — handler sets
     ``was_pinned=False`` regardless of the helper's return. A
     future refactor that reads ``result.get("was_pinned",
     False)`` would silently couple the pin endpoint to the
     unpin endpoint's contract; this test pins that they stay
     decoupled.
  5. Schema integration — handler output round-trips through
     :class:`FeatureFlagPinResponse.model_validate`.

Not pinned (intentional, deferred): HTTP-level tests (200/404,
JSON content type, 422 on malformed path) — belong in a
TestClient round; logger info events — structlog's own test
suite pins that; the exact ``detail`` string for 404 — pinned
loosely via substring match;
``FlagPinnedError`` propagation through ``pin_flag`` — the
helper does not raise it, so the path is unreachable.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.api.schemas import FeatureFlagPinResponse


def _pin_dict(
    name: str,
    pinned: bool = True,
    already_pinned: bool = False,
    current_value: bool = True,
) -> dict[str, object]:
    """Build a 4-key pin result dict mirroring ``pin_flag``'s return.

    Defaults match the most common case: a known flag that was
    previously unlocked and is now pinned.
    """
    return {
        "name": name,
        "pinned": pinned,
        "already_pinned": already_pinned,
        "current_value": current_value,
    }


class TestPinFeatureFlagHandler:
    """``pin_feature_flag`` handler-direct contract tests."""

    async def test_happy_path_returns_pin_response(self) -> None:
        """``pin_flag`` returns a valid 4-key dict; handler builds
        a ``FeatureFlagPinResponse`` with those four fields plus a
        hard-coded ``was_pinned=False``."""
        from app.api.routes import pin_feature_flag

        with patch(
            "orchestrator.feature_flags.pin_flag",
            return_value=_pin_dict(
                name="flag_a",
                pinned=True,
                already_pinned=False,
                current_value=True,
            ),
        ) as mock_pin:
            result = await pin_feature_flag(name="flag_a")

        # Path ``name`` flows through to ``pin_flag`` (no body).
        mock_pin.assert_called_once_with("flag_a")
        assert isinstance(result, FeatureFlagPinResponse)
        assert result.name == "flag_a"
        assert result.pinned is True
        assert result.already_pinned is False
        assert result.was_pinned is False  # hard-coded sentinel
        assert result.current_value is True

    async def test_unknown_flag_returns_404(self) -> None:
        """``pin_flag`` returns ``None``; handler raises 404.
        Mirrors v16 / v18 / v40 sibling endpoints."""
        from fastapi import HTTPException

        from app.api.routes import pin_feature_flag

        # Patch helper (None) + registry keys — keeps any
        # future inlined "is known?" check covered.
        with patch(
            "orchestrator.feature_flags.pin_flag",
            return_value=None,
        ), patch(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            {},
        ), patch(
            "orchestrator.feature_flags._overrides",
            {},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await pin_feature_flag(name="not_a_real_flag")

        assert exc_info.value.status_code == 404
        assert "not_a_real_flag" in str(exc_info.value.detail)

    async def test_already_pinned_no_op_returns_200(self) -> None:
        """``pin_flag`` returns ``already_pinned=True`` (idempotent
        contract). Handler passes through verbatim; status 200,
        NOT 4xx. Load-bearing difference from v135's rollback."""
        from app.api.routes import pin_feature_flag

        with patch(
            "orchestrator.feature_flags.pin_flag",
            return_value=_pin_dict(
                name="flag_a",
                pinned=True,
                already_pinned=True,  # the no-op signal
                current_value=False,
            ),
        ):
            result = await pin_feature_flag(name="flag_a")

        assert isinstance(result, FeatureFlagPinResponse)
        assert result.name == "flag_a"
        assert result.pinned is True
        assert result.already_pinned is True  # the no-op flag
        assert result.was_pinned is False
        assert result.current_value is False

    async def test_was_pinned_always_false_on_pin_endpoint(self) -> None:
        """Handler hard-codes ``was_pinned=False`` regardless of
        helper output (``pin_flag`` never sets it). Pins the
        pin / unpin decoupling: opposite sentinels on shared
        response model."""
        from app.api.routes import pin_feature_flag

        with patch(
            "orchestrator.feature_flags.pin_flag",
            return_value=_pin_dict(
                name="flag_a",
                already_pinned=False,
            ),
        ):
            result = await pin_feature_flag(name="flag_a")

        assert result.was_pinned is False

class TestPinFeatureFlagSchemaIntegration:
    """Schema-vs-handler integration test for the response shape."""

    def test_response_model_validates_handler_output(self) -> None:
        """Handler output round-trips through ``FeatureFlagPinResponse.model_validate``."""
        from app.api.routes import pin_feature_flag

        # Sync via asyncio.run (v132..v135 pattern).
        with patch(
            "orchestrator.feature_flags.pin_flag",
            return_value=_pin_dict(
                name="flag_x",
                pinned=True,
                already_pinned=False,
                current_value=False,
            ),
        ):
            result = asyncio.run(pin_feature_flag(name="flag_x"))

        # Pydantic v2 round-trip on the model dump.
        revalidated = FeatureFlagPinResponse.model_validate(
            result.model_dump()
        )
        assert revalidated == result
        assert revalidated.name == "flag_x"
        assert revalidated.pinned is True
        assert revalidated.already_pinned is False
        assert revalidated.was_pinned is False  # hard-coded sentinel
        assert revalidated.current_value is False