"""Tests for the GET /v1/feature_flags/history endpoint (handler-direct).

Companion to the v131 ``test_flag_history_response_schemas`` (schema-only)
and v132 ``test_get_feature_flags`` (snapshot handler-direct) rounds:
v131 pinned the schema wire-shape, v132 pinned the read-the-now sibling,
this round pins the read-the-audit-log handler for ``/history``.

Mirrors the ``test_list_packs.py`` / ``test_get_feature_flags.py``
pattern: import the route handler, patch the source module it reaches
into (``orchestrator.feature_flags``), call the handler, assert the
response. No TestClient — the handler is trivial and exercising it
directly gives the test a single seam that survives any future
APIRouter / dep-injection reshuffle.

What is pinned here:

  1. Happy path — three events, no filter, default ``limit`` (100),
     the handler returns them in the SAME order ``get_history``
     returned them (newest-first — already reversed by
     ``get_history``). ``total == len(entries)``.
  2. Empty log — ``get_history()`` returns ``[]`` → handler still
     returns ``entries=[]`` and ``total=0`` (mirrors
     ``PacksResponse`` / ``KnownPhasesResponse`` defensive-empty
     pattern; the audit log is a query, not a registry lookup,
     and "no rows match" is a legitimate result).
  3. ``flag_name`` filter — three events for two distinct flags;
     filtering to one name returns only those events AND
     ``total`` reflects the FILTERED count (not the pre-filter
     count). This is the most important contract: ``total`` is
     "rows that matched", not "rows in the rolling buffer".
  4. ``flag_name`` filter for an unknown name — ``total=0``,
     ``entries=[]`` (NOT a 404). The handler treats the audit
     log as a query, not a registry lookup, so "no rows match"
     is the legitimate empty result.
  5. ``limit`` clamps ``entries`` but NOT ``total`` — five events
     with ``limit=2`` → ``len(entries) == 2`` BUT ``total == 5``.
     The schema docstring explicitly says ``total`` is "BEFORE
     the limit clamp is applied" so a dashboard can detect that
     the history has grown past the page size; this test is the
     one that would catch a regression where the handler
     accidentally sets ``total = len(page)``.
  6. ``limit`` larger than total — ``limit=1000`` with two events
     → ``len(entries) == 2`` and ``total == 2`` (no IndexError).
     Slicing with a value larger than ``len(history)`` is the
     standard Python list semantics; pin it so a future refactor
     that introduces an explicit length check doesn't reject
     ``limit=1000`` as "too many".
  7. Field round-trip — every :class:`FlagHistoryEntry` field
     (``name``, ``value``, ``reason``, ``actor``) is mapped
     correctly from the source :class:`FlagOverride`. The
     schema docstring promises a one-for-one mapping and the
     dataclass field names are identical — a regression that
     drops a field (or mis-spells one) would surface as a
     schema validation error or an empty field in the response.
  8. Schema integration — the handler's return value
     round-trips through ``FlagHistoryResponse.model_validate``,
     confirming the structural contract holds.

Not pinned (intentional, deferred):

  - ``logger.info("api.feature_flag.history_read", ...)`` call
    shape — structlog's own test suite pins that.
  - HTTP-level tests (200 status, JSON content type) — those
    belong in a TestClient round (deferred to v133+; the
    handler-direct round is sufficient for the v133 seam).
  - The branch-vs-master response-shape divergence documented
    in :class:`FlagHistoryResponse`'s docstring (newest-first
    vs. oldest-first, dataclass vs. dict) — that's a
    contract-test against the schemas, not the handler.
  - ``limit=0`` rejection — FastAPI's ``Query(ge=1)`` rejects
    it with a 422 BEFORE the handler runs, so the handler is
    never reached. Pin it at the TestClient layer if a future
    round wants to assert the 422.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.api.schemas import FlagHistoryResponse
from orchestrator.feature_flags import FlagOverride


def _make_event(name: str, value: bool, reason: str, actor: str) -> FlagOverride:
    """Build a FlagOverride for test fixtures.

    Defined at module scope (not as a fixture) because every test
    constructs its own events with bespoke values; a fixture would
    just add an indirection without saving any code.
    """
    return FlagOverride(name=name, value=value, reason=reason, actor=actor)


class TestGetFeatureFlagHistoryHandler:
    """``get_feature_flag_history`` handler-direct contract tests."""

    async def test_happy_path_three_events_newest_first(self) -> None:
        """Three events, no filter, default limit, returned in
        the same order ``get_history`` returned them
        (newest-first — the rolling buffer is reversed inside
        ``get_history``), ``total`` matches ``len(entries)``."""
        from app.api.routes import get_feature_flag_history

        events = [
            _make_event("flag_c", False, "set_flag", "system"),
            _make_event("flag_b", True, "pin_flag", "alice"),
            _make_event("flag_a", True, "set_flag", "system"),
        ]

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=events,
        ):
            result = await get_feature_flag_history()

        assert isinstance(result, FlagHistoryResponse)
        assert result.total == 3
        assert len(result.entries) == 3
        # ``get_history`` already returns newest-first; the handler
        # does NOT re-reverse. Pin the wire contract: index 0 is
        # the first row the mock returned.
        assert [e.name for e in result.entries] == [
            "flag_c",
            "flag_b",
            "flag_a",
        ]
        assert [e.value for e in result.entries] == [False, True, True]

    async def test_empty_history_returns_empty_response(self) -> None:
        """When the audit log is empty (e.g. a fresh process), the
        endpoint returns ``entries=[]`` and ``total=0`` rather
        than raising. The audit log is a query, not a registry
        lookup, so "no rows match" is the legitimate empty
        result — NOT a 404."""
        from app.api.routes import get_feature_flag_history

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=[],
        ):
            result = await get_feature_flag_history()

        assert result.entries == []
        assert result.total == 0

    async def test_flag_name_filter_returns_only_matching_events(self) -> None:
        """When ``flag_name`` is supplied, ``get_history`` is
        expected to return only the matching events, and the
        handler must reflect ``total`` as the FILTERED count
        (not the pre-filter count). This is the most important
        contract: ``total`` is "rows that matched", not "rows in
        the rolling buffer"."""
        from app.api.routes import get_feature_flag_history

        # The real ``get_history(name=...)`` filters internally,
        # so the mock receives the already-filtered list and the
        # handler's ``total = len(history)`` reflects the
        # filtered count. We model that here.
        filtered = [
            _make_event("flag_a", True, "set_flag", "system"),
            _make_event("flag_a", False, "rollback", "alice"),
        ]

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=filtered,
        ) as mock_get_history:
            result = await get_feature_flag_history(flag_name="flag_a")

        mock_get_history.assert_called_once_with(name="flag_a")
        assert result.total == 2
        assert len(result.entries) == 2
        assert all(e.name == "flag_a" for e in result.entries)
        assert [e.value for e in result.entries] == [True, False]

    async def test_flag_name_filter_for_unknown_name(self) -> None:
        """``flag_name`` for a name that has no rows returns
        ``total=0`` and ``entries=[]`` (NOT a 404). The audit
        log is a query, not a registry lookup — "no rows match"
        is a legitimate result that lets dashboards distinguish
        "the filter matched nothing" from "the log is empty"."""
        from app.api.routes import get_feature_flag_history

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=[],
        ):
            result = await get_feature_flag_history(flag_name="nonexistent")

        assert result.entries == []
        assert result.total == 0

    async def test_limit_clamps_entries_but_not_total(self) -> None:
        """The handler slices ``history[:limit]`` for ``entries``
        but computes ``total`` BEFORE the limit is applied, so
        ``total`` lets a caller detect that the history has
        grown past the page size. This is the most important
        behaviour contract of the endpoint; pin it here so a
        refactor that confuses ``page`` and ``history`` is
        caught immediately."""
        from app.api.routes import get_feature_flag_history

        events = [
            _make_event("flag_e", True, "set_flag", "system"),
            _make_event("flag_d", True, "set_flag", "system"),
            _make_event("flag_c", False, "rollback", "alice"),
            _make_event("flag_b", True, "set_flag", "system"),
            _make_event("flag_a", True, "set_flag", "system"),
        ]

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=events,
        ):
            result = await get_feature_flag_history(limit=2)

        assert result.total == 5, (
            "total must reflect the FULL filtered count, NOT "
            "the post-limit page size"
        )
        assert len(result.entries) == 2
        # The page is the FIRST N (newest), so we expect
        # flag_e then flag_d.
        assert [e.name for e in result.entries] == ["flag_e", "flag_d"]

    async def test_limit_larger_than_total_is_safe(self) -> None:
        """``limit`` larger than ``len(history)`` is safe — the
        standard Python slicing semantics return the full list
        without an IndexError. Pin it so a future refactor that
        introduces an explicit length check doesn't reject
        ``limit=1000`` as "too many"."""
        from app.api.routes import get_feature_flag_history

        events = [
            _make_event("flag_b", False, "rollback", "alice"),
            _make_event("flag_a", True, "set_flag", "system"),
        ]

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=events,
        ):
            result = await get_feature_flag_history(limit=1000)

        assert result.total == 2
        assert len(result.entries) == 2
        assert [e.name for e in result.entries] == ["flag_b", "flag_a"]

    async def test_field_round_trip_from_flag_override(self) -> None:
        """Every :class:`FlagHistoryEntry` field (``name``,
        ``value``, ``reason``, ``actor``) is mapped correctly
        from the source :class:`FlagOverride`. The schema
        docstring promises a one-for-one mapping and the
        dataclass field names are identical — a regression
        that drops or mis-spells a field would surface here."""
        from app.api.routes import get_feature_flag_history

        source = _make_event(
            name="t2_three_judge_panel",
            value=False,
            reason="manual rollback",
            actor="alice",
        )

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=[source],
        ):
            result = await get_feature_flag_history()

        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.name == "t2_three_judge_panel"
        assert entry.value is False
        assert entry.reason == "manual rollback"
        assert entry.actor == "alice"


class TestGetFeatureFlagHistorySchemaIntegration:
    """Schema-vs-handler integration tests for the response shape."""

    def test_response_model_validates_handler_output(self) -> None:
        """The handler's return value satisfies the
        :class:`FlagHistoryResponse` Pydantic contract — guards
        against a future handler refactor that drops a required
        field (``entries`` or ``total``) or changes the entry
        shape."""
        from app.api.routes import get_feature_flag_history

        # We don't await here — the handler is async, and the
        # schema-validity check is purely structural. Use a sync
        # call site via ``asyncio.run`` to keep the test file
        # dependency-free (no pytest-asyncio requirement on this
        # one method). The rest of the class uses bare
        # ``async def`` because ``pyproject.toml`` sets
        # ``asyncio_mode = "auto"``.
        import asyncio

        events = [
            _make_event("flag_x", True, "set_flag", "system"),
        ]

        with patch(
            "orchestrator.feature_flags.get_history",
            return_value=events,
        ):
            result = asyncio.run(get_feature_flag_history())

        # Pydantic v2 round-trip — re-validate the already-built
        # model instance to confirm it conforms to the schema.
        revalidated = FlagHistoryResponse.model_validate(result.model_dump())
        assert revalidated == result
        assert revalidated.total == 1
        assert len(revalidated.entries) == 1
        assert revalidated.entries[0].name == "flag_x"
        assert revalidated.entries[0].value is True