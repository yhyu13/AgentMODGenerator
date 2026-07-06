"""Tests for the POST /v1/feature_flags/{name}/unpin endpoint (handler-direct).

Companion to v132 (read-now), v133 (read-history), v134 (toggle),
v135 (rollback), v136 (pin): v137 unpins the fourth Session 5
mutation. Pattern mirrors v134 / v135 / v136: import the route
handler, patch the source module it reaches into
(``orchestrator.feature_flags``) — the helper AND the registry keys
(``_DEFAULT_FLAGS`` / ``_overrides``) — call the handler, assert the
response. The registry is patched not because the unpin handler
inspects it directly (``unpin_flag`` raises the unknown-flag signal
via its ``None`` return) but so any future inlined "is known?" check
would be caught by the same setup. No TestClient — short handler,
single seam.

What is pinned:

  1. Happy path — ``unpin_flag`` returns a 4-key dict
     ``{name, pinned, was_pinned, current_value}`` (``pinned=False``
     always). Handler builds a ``FeatureFlagPinResponse`` with those
     four fields plus a hard-coded ``already_pinned=False``
     (``unpin_flag`` never sets ``already_pinned`` — owned by
     ``pin``). Pins the wire shape = four helper fields + the False
     sentinel, MIRROR of v136.
  2. Unknown flag — ``unpin_flag`` returns ``None``; handler raises
     ``HTTPException(status_code=404)``. Mirrors v16 / v18 / v40 /
     v41 sibling contracts.
  3. Not-pinned no-op — ``unpin_flag`` returns ``was_pinned=False``
     (idempotent contract). Handler passes through verbatim; status
     200, NOT 4xx. Mirror of v136's already-pinned no-op test.
  4. ``already_pinned`` hard-coded to False — handler sets
     ``already_pinned=False`` regardless of the helper's return.
     Inverse of v136's was_pinned sentinel test; pins the pin /
     unpin decoupling.
  5. Schema integration — handler output round-trips through
     :class:`FeatureFlagPinResponse.model_validate`.

Not pinned (intentional, deferred): HTTP-level tests (200/404,
JSON content type, 422 on malformed path) — belong in a
TestClient round; logger info events — structlog's own test
suite pins that; the exact ``detail`` string for 404 — pinned
loosely via substring match;
``FlagPinnedError`` propagation through ``unpin_flag`` — the
helper does not raise it, so the path is unreachable.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.api.schemas import FeatureFlagPinResponse


def _unpin_dict(
    name: str,
    pinned: bool = False,
    was_pinned: bool = True,
    current_value: bool = True,
) -> dict[str, object]:
    """Build a 4-key unpin result dict mirroring ``unpin_flag``'s return.

    Defaults match the most common case: a known flag that was
    previously locked and is now unpinned (``was_pinned=True``,
    ``pinned=False``).
    """
    return {
        "name": name,
        "pinned": pinned,
        "was_pinned": was_pinned,
        "current_value": current_value,
    }


class TestUnpinFeatureFlagHandler:
    """``unpin_feature_flag`` handler-direct contract tests."""

    async def test_happy_path_returns_unpin_response(self) -> None:
        """``unpin_flag`` returns a valid 4-key dict; handler builds
        a ``FeatureFlagPinResponse`` with those four fields plus a
        hard-coded ``already_pinned=False``."""
        from app.api.routes import unpin_feature_flag

        with patch(
            "orchestrator.feature_flags.unpin_flag",
            return_value=_unpin_dict(
                name="flag_a",
                pinned=False,
                was_pinned=True,
                current_value=True,
            ),
        ) as mock_unpin:
            result = await unpin_feature_flag(name="flag_a")

        # Path ``name`` flows through to ``unpin_flag`` (no body).
        mock_unpin.assert_called_once_with("flag_a")
        assert isinstance(result, FeatureFlagPinResponse)
        assert result.name == "flag_a"
        assert result.pinned is False
        assert result.already_pinned is False  # hard-coded sentinel
        assert result.was_pinned is True
        assert result.current_value is True

    async def test_unknown_flag_returns_404(self) -> None:
        """``unpin_flag`` returns ``None``; handler raises 404.
        Mirrors v16 / v18 / v40 / v41 sibling endpoints."""
        from fastapi import HTTPException

        from app.api.routes import unpin_feature_flag

        # Patch helper (None) + registry keys — keeps any
        # future inlined "is known?" check covered.
        with patch(
            "orchestrator.feature_flags.unpin_flag",
            return_value=None,
        ), patch(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            {},
        ), patch(
            "orchestrator.feature_flags._overrides",
            {},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await unpin_feature_flag(name="not_a_real_flag")

        assert exc_info.value.status_code == 404
        assert "not_a_real_flag" in str(exc_info.value.detail)

    async def test_not_pinned_no_op_returns_200(self) -> None:
        """``unpin_flag`` returns ``was_pinned=False`` (idempotent
        contract). Handler passes through verbatim; status 200,
        NOT 4xx. Mirror of v136's already-pinned no-op test."""
        from app.api.routes import unpin_feature_flag

        with patch(
            "orchestrator.feature_flags.unpin_flag",
            return_value=_unpin_dict(
                name="flag_a",
                pinned=False,
                was_pinned=False,  # the no-op signal
                current_value=False,
            ),
        ):
            result = await unpin_feature_flag(name="flag_a")

        assert isinstance(result, FeatureFlagPinResponse)
        assert result.name == "flag_a"
        assert result.pinned is False
        assert result.already_pinned is False  # hard-coded sentinel
        assert result.was_pinned is False  # the no-op flag
        assert result.current_value is False

    async def test_already_pinned_always_false_on_unpin_endpoint(self) -> None:
        """Handler hard-codes ``already_pinned=False`` regardless of
        helper output (``unpin_flag`` never sets it). Inverse of
        v136's was_pinned sentinel test; pins the pin / unpin
        decoupling: opposite sentinels on shared response model."""
        from app.api.routes import unpin_feature_flag

        with patch(
            "orchestrator.feature_flags.unpin_flag",
            return_value=_unpin_dict(
                name="flag_a",
                was_pinned=True,
            ),
        ):
            result = await unpin_feature_flag(name="flag_a")

        assert result.already_pinned is False

class TestUnpinFeatureFlagSchemaIntegration:
    """Schema-vs-handler integration test for the response shape."""

    def test_response_model_validates_handler_output(self) -> None:
        """Handler output round-trips through ``FeatureFlagPinResponse.model_validate``."""
        from app.api.routes import unpin_feature_flag

        # Sync via asyncio.run (v132..v136 pattern).
        with patch(
            "orchestrator.feature_flags.unpin_flag",
            return_value=_unpin_dict(
                name="flag_x",
                pinned=False,
                was_pinned=True,
                current_value=False,
            ),
        ):
            result = asyncio.run(unpin_feature_flag(name="flag_x"))

        # Pydantic v2 round-trip on the model dump.
        revalidated = FeatureFlagPinResponse.model_validate(
            result.model_dump()
        )
        assert revalidated == result
        assert revalidated.name == "flag_x"
        assert revalidated.pinned is False
        assert revalidated.already_pinned is False  # hard-coded sentinel
        assert revalidated.was_pinned is True
        assert revalidated.current_value is False