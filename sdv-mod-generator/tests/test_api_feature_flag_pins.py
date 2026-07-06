"""Tests for the GET /v1/feature_flags/pins endpoint (handler-direct).

Companion to v132..v138 (the prior Session 5 rounds). v139 pins
the read-only COLLECTION pin-state snapshot. Pattern mirrors
v132 / v138: import the route handler, patch
``orchestrator.feature_flags`` — the ``get_pinned_flags`` helper
that supplies the sorted name tuple AND the ``is_enabled`` helper
that supplies the per-flag value — call the handler, assert the
response. No TestClient — short handler, single seam.

Pinned contracts (7):

  1. Empty collection — ``get_pinned_flags()`` returns ``()``;
     response is ``FeatureFlagPinsResponse(pins=[], count=0)``,
     NOT 404 (mirrors the v15 ``GET /v1/feature_flags``
     empty-set contract).
  2. Single pinned flag — one ``FeatureFlagPinSummary`` entry,
     ``count == 1``.
  3. Multiple pinned — ``pins`` mirrors ``get_pinned_flags()`` in
     helper order (the helper returns a sorted tuple, so the
     response is sorted-by-name); ``count`` equals ``len(pins)``.
  4. ``is_enabled`` called PER name — refactor that hoisted the
     call outside the comprehension would lose per-flag values.
  5. Mixed on/off values — pins can be locked at either state;
     each entry is a ``FeatureFlagPinSummary`` (not a
     ``FeatureFlagValue``).
  6. ``count`` is ``len(pins)`` — explicit redundancy pin.
  7. Schema integration — handler output round-trips through
     :class:`FeatureFlagPinsResponse.model_validate`.

Not pinned (deferred): HTTP-level tests, structlog events,
exact wire-shape field order, sort stability under duplicates
(the helper returns a deduped tuple by construction).
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.api.schemas import FeatureFlagPinSummary, FeatureFlagPinsResponse


def _patch_pins(
    *,
    pinned: tuple[str, ...] = ("flag_a",),
    enabled: dict[str, bool] | None = None,
) -> ExitStack:
    """Patch the two seams ``get_feature_flag_pins`` reads from.

    ``pinned`` is the sorted tuple ``get_pinned_flags()`` returns
    (the handler does not re-sort). ``enabled`` is a per-name map
    that the patched ``is_enabled`` consults via ``.get``; missing
    keys default to ``False`` (mirrors the real helper's
    deny-by-default fallback) so a test that forgets to seed a
    flag's value fails loudly rather than passing on a stale True.
    """
    if enabled is None:
        enabled = {"flag_a": True}
    stack = ExitStack()
    stack.enter_context(
        patch(
            "orchestrator.feature_flags.get_pinned_flags",
            return_value=pinned,
        )
    )
    stack.enter_context(
        patch(
            "orchestrator.feature_flags.is_enabled",
            side_effect=lambda name: enabled.get(name, False),
        )
    )
    return stack


class TestGetFeatureFlagPinsHandler:
    """``get_feature_flag_pins`` handler-direct contract tests."""

    async def test_empty_collection_returns_empty_pins(self) -> None:
        """No flags pinned → ``pins=[]``, ``count=0``, status 200.

        The handler docstring is explicit: an empty collection is
        ``200 OK`` with ``{"pins": [], "count": 0}``, NOT a 404,
        so dashboards can render "no flags pinned" without
        special-casing the error path."""
        from app.api.routes import get_feature_flag_pins

        with _patch_pins(pinned=()):
            result = await get_feature_flag_pins()

        assert isinstance(result, FeatureFlagPinsResponse)
        assert result.pins == []
        assert result.count == 0

    async def test_single_pinned_flag_round_trips(self) -> None:
        """One flag pinned → one ``FeatureFlagPinSummary`` entry,
        ``count == 1``. Mirrors the v132 single-flag snapshot,
        but flattened into a collection."""
        from app.api.routes import get_feature_flag_pins

        with _patch_pins(pinned=("flag_a",), enabled={"flag_a": True}):
            result = await get_feature_flag_pins()

        assert len(result.pins) == 1
        assert result.count == 1
        assert result.pins[0].name == "flag_a"
        assert result.pins[0].current_value is True

    async def test_multiple_pinned_preserve_helper_order(self) -> None:
        """Multiple pins → ``pins`` mirrors ``get_pinned_flags()``
        in order (sorted-by-name). ``count`` matches ``len(pins)``.
        Pins a deterministic-ordering contract for dashboard
        snapshot diffs."""
        from app.api.routes import get_feature_flag_pins

        pinned = ("alpha", "beta", "gamma")
        enabled = {"alpha": True, "beta": False, "gamma": True}
        with _patch_pins(pinned=pinned, enabled=enabled):
            result = await get_feature_flag_pins()

        assert [p.name for p in result.pins] == ["alpha", "beta", "gamma"]
        assert result.count == 3
        assert [p.current_value for p in result.pins] == [True, False, True]

    async def test_is_enabled_called_per_flag_name(self) -> None:
        """The handler calls ``is_enabled(name)`` inside the list
        comprehension, so each pin's ``current_value`` reflects
        the per-flag lookup. A refactor that hoisted it out would
        silently drop the per-flag mapping."""
        from app.api.routes import get_feature_flag_pins

        pinned = ("flag_a", "flag_b", "flag_c")
        # Each name maps to a distinct value; if the handler read
        # a single helper result, two of the three would be wrong.
        enabled = {"flag_a": True, "flag_b": False, "flag_c": True}
        with _patch_pins(pinned=pinned, enabled=enabled):
            result = await get_feature_flag_pins()

        by_name = {p.name: p.current_value for p in result.pins}
        assert by_name == enabled  # every per-flag lookup round-tripped

    async def test_mixed_on_off_values_round_trip(self) -> None:
        """A pinned flag is locked, not forced-on. ``current_value``
        carries the LIVE on/off state; each entry is a
        ``FeatureFlagPinSummary`` (not a ``FeatureFlagValue``)."""
        from app.api.routes import get_feature_flag_pins

        pinned = ("on_flag", "off_flag")
        enabled = {"on_flag": True, "off_flag": False}
        with _patch_pins(pinned=pinned, enabled=enabled):
            result = await get_feature_flag_pins()

        assert result.pins[0].current_value is True
        assert result.pins[1].current_value is False
        assert all(isinstance(p, FeatureFlagPinSummary) for p in result.pins)

    async def test_count_equals_len_pins(self) -> None:
        """``count`` is ``len(pins)``. The handler computes both
        from the same comprehension so they cannot drift in
        practice — but pinning the equality explicitly catches
        any future counter-drift refactor."""
        from app.api.routes import get_feature_flag_pins

        pinned = ("a", "b", "c", "d")
        enabled = {n: True for n in pinned}
        with _patch_pins(pinned=pinned, enabled=enabled):
            result = await get_feature_flag_pins()

        assert result.count == len(result.pins) == 4


class TestGetFeatureFlagPinsSchemaIntegration:
    """Schema-vs-handler integration test for the collection response."""

    def test_response_model_validates_handler_output(self) -> None:
        """Handler output round-trips through
        :class:`FeatureFlagPinsResponse.model_validate`. Pins the
        wire shape: 2-field top-level (``pins`` + ``count``) with
        each entry a :class:`FeatureFlagPinSummary` (``name`` +
        ``current_value``)."""
        from app.api.routes import get_feature_flag_pins

        pinned = ("alpha", "beta")
        enabled = {"alpha": True, "beta": False}
        # Sync via asyncio.run (v132..v138 pattern).
        with _patch_pins(pinned=pinned, enabled=enabled):
            result = asyncio.run(get_feature_flag_pins())

        revalidated = FeatureFlagPinsResponse.model_validate(
            result.model_dump()
        )
        assert revalidated == result
        assert revalidated.count == 2
        assert [p.name for p in revalidated.pins] == ["alpha", "beta"]
        assert [p.current_value for p in revalidated.pins] == [True, False]