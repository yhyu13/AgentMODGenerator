"""Schema-level tests for the Session 5 flag-history response schemas.

Companion to the v33 schema port — pins the Pydantic contract that the
``GET /v1/feature_flags/history`` handler emits. Schema-only
(no TestClient, no handler import, no ``orchestrator.feature_flags``
import) because the route handler touches the in-memory
:func:`orchestrator.feature_flags.get_history` ring buffer; this
round pins the wire shape only, the next round (v34) pins the
handler-direct contract.

Mirrors the v54 (``test_estimates_response_schemas.py``), v55
(``test_prompt_estimate_response_schemas.py``), and v60
(``test_phase_detail_response_schema.py``) pattern. Splits from the
handler-direct test the same way those split from their handler
tests: this round pins schema invariants, the handler round pins
the orchestrator-contract.

Five invariants pinned here:

  1. ``FlagHistoryEntry`` happy path — all 4 fields populated and
     round-trip unchanged. ``name`` is the snake_case flag id;
     ``value`` is the post-override bool; ``reason`` is the
     free-text justification; ``actor`` is the operator handle.
  2. ``FlagHistoryEntry`` off / on ``value`` — both polarities
     round-trip; the field accepts a literal ``False`` (a no-op
     write that flipped a flag that was already off surfaces as a
     fresh ``False`` entry, not as a missing entry).
  3. ``FlagHistoryResponse`` empty envelope — ``entries=[]``,
     ``total=0`` is the canonical "log is empty" shape. The
     ``docstring`` explicitly promises ``total == 0`` is
     distinguishable from "filter matched nothing" without a
     round-trip — pin it.
  4. ``FlagHistoryResponse`` populated envelope — N entries,
     ``total == N`` (the pre-limit count equals the post-limit
     count when the matching set fits in one page).
  5. ``FlagHistoryResponse.total`` numeric guard — ``ge=0`` means
     negative totals are rejected. Boundary: 0 is ok.

Not pinned here (intentional, deferred): ``entries`` ordering
(newest-first is enforced by ``orchestrator.feature_flags.get_history``
itself, not by the schema), ``reason`` / ``actor`` empty-string
acceptance (the schema uses ``str`` without ``min_length``), long
string acceptance (no ``max_length``), JSON round-trip via
``model_dump_json`` (Pydantic's own test suite pins that), and
``FlagHistoryEntry`` cross-validation that ``value`` actually
mirrors ``orchestrator.feature_flags.FlagOverride.value``
(handler-level test concern, not a schema concern).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    FlagHistoryEntry,
    FlagHistoryResponse,
)


class TestFlagHistoryEntryRoundTrip:
    """``FlagHistoryEntry`` is one row of the in-memory audit log."""

    def test_full_entry_round_trip(self) -> None:
        # All 4 fields populated — verifies the schema accepts the
        # full field set and round-trips each value unchanged.
        e = FlagHistoryEntry(
            name="t2_three_judge_panel",
            value=True,
            reason="manual rollback",
            actor="alice",
        )
        assert e.name == "t2_three_judge_panel"
        assert e.value is True
        assert e.reason == "manual rollback"
        assert e.actor == "alice"

    def test_value_false_round_trip(self) -> None:
        # A no-op write that flipped a flag from on→off (or off→off)
        # surfaces as ``value=False`` on the most recent entry. Pin
        # that a literal ``False`` is preserved (no truthy-default
        # coercion that would erase the off-state in a JSON round-trip).
        e = FlagHistoryEntry(
            name="security_headers_middleware",
            value=False,
            reason="pin_flag",
            actor="system",
        )
        assert e.value is False
        assert e.name == "security_headers_middleware"
        assert e.reason == "pin_flag"
        assert e.actor == "system"


class TestFlagHistoryResponseEmpty:
    """``FlagHistoryResponse`` empty envelope — log is genuinely empty."""

    def test_empty_envelope_round_trip(self) -> None:
        # ``entries=[]`` and ``total=0`` is the canonical "log is
        # empty" shape. The docstring explicitly promises
        # ``total == 0`` is distinguishable from "filter matched
        # nothing" without a round-trip — pin both fields.
        r = FlagHistoryResponse(entries=[], total=0)
        assert r.entries == []
        assert r.total == 0

    def test_empty_envelope_preserves_field_types(self) -> None:
        # Defensive: ``entries`` must remain a ``list`` (not a
        # ``None`` coercion that Pydantic v2 might apply on missing
        # fields). ``total`` must remain an ``int``.
        r = FlagHistoryResponse(entries=[], total=0)
        assert isinstance(r.entries, list)
        assert isinstance(r.total, int)


class TestFlagHistoryResponsePopulated:
    """``FlagHistoryResponse`` populated envelope — N entries, ``total == N``."""

    def test_single_entry_envelope_round_trip(self) -> None:
        # The minimal non-empty case — one entry, total == 1.
        # Verifies that ``FlagHistoryResponse`` accepts a nested
        # ``FlagHistoryEntry`` instance without a transformation
        # layer (the wire shape is the entry's shape directly).
        entry = FlagHistoryEntry(
            name="t2_three_judge_panel",
            value=True,
            reason="set_flag",
            actor="bob",
        )
        r = FlagHistoryResponse(entries=[entry], total=1)
        assert r.entries == [entry]
        assert r.entries[0].name == "t2_three_judge_panel"
        assert r.entries[0].value is True
        assert r.total == 1

    def test_multi_entry_envelope_round_trip(self) -> None:
        # The realistic case — multiple entries (newest-first per
        # ``orchestrator.feature_flags.get_history``'s contract;
        # this test does not pin the order, only that the schema
        # round-trips the list intact).
        entries = [
            FlagHistoryEntry(
                name="security_headers_middleware",
                value=True,
                reason="set_flag",
                actor="carol",
            ),
            FlagHistoryEntry(
                name="t2_three_judge_panel",
                value=False,
                reason="rollback",
                actor="alice",
            ),
            FlagHistoryEntry(
                name="discord_webhook_signature",
                value=True,
                reason="pin_flag",
                actor="system",
            ),
        ]
        r = FlagHistoryResponse(entries=entries, total=3)
        assert r.entries == entries
        assert len(r.entries) == 3
        assert r.total == 3
        # Per-entry field round-trip — pin that the schema does not
        # coerce ``value`` to a string or any other type along the way.
        assert r.entries[0].value is True
        assert r.entries[1].value is False
        assert r.entries[2].actor == "system"

    def test_total_can_exceed_page_size(self) -> None:
        # ``total`` reflects the count BEFORE the ``limit`` clamp
        # is applied — the wire shape must allow ``len(entries) <
        # total`` so a caller can detect that the log has wrapped.
        # Pin with a synthetic 1-entry / total=42 envelope (a real
        # handler would never construct this, but the schema is the
        # contract — it must permit it).
        entry = FlagHistoryEntry(
            name="x", value=True, reason="r", actor="a",
        )
        r = FlagHistoryResponse(entries=[entry], total=42)
        assert len(r.entries) == 1
        assert r.total == 42


class TestFlagHistoryResponseTotalGuard:
    """``FlagHistoryResponse.total`` has ``ge=0`` — boundary and negative."""

    def test_total_zero_is_ok(self) -> None:
        # Boundary: 0 is the canonical empty-log count. Already
        # covered by the empty-envelope test above, but pin it
        # again here as part of the numeric-guard class for
        # symmetry with the other schema tests.
        FlagHistoryResponse(entries=[], total=0)

    def test_total_negative_is_rejected(self) -> None:
        # Negative totals are nonsense — the ``ge=0`` guard on the
        # ``total`` field must reject them. ``entries=[]`` keeps
        # the rejection focused on the ``total`` field.
        with pytest.raises(ValidationError):
            FlagHistoryResponse(entries=[], total=-1)


class TestFlagHistoryEntryRequiredFields:
    """``FlagHistoryEntry`` fields without defaults — omitting any one is a ``ValidationError``."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(
                {"value": True, "reason": "set_flag", "actor": "alice"},
                id="missing_name",
            ),
            pytest.param(
                {"name": "t2_three_judge_panel", "reason": "set_flag",
                 "actor": "alice"},
                id="missing_value",
            ),
            pytest.param(
                {"name": "t2_three_judge_panel", "value": True,
                 "actor": "alice"},
                id="missing_reason",
            ),
            pytest.param(
                {"name": "t2_three_judge_panel", "value": True,
                 "reason": "set_flag"},
                id="missing_actor",
            ),
        ],
    )
    def test_missing_required_field_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            FlagHistoryEntry(**kwargs)  # type: ignore[arg-type]


class TestFlagHistoryResponseRequiredFields:
    """``FlagHistoryResponse`` fields without defaults — omitting any one is a ``ValidationError``."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"total": 0}, id="missing_entries"),
            pytest.param({"entries": []}, id="missing_total"),
        ],
    )
    def test_missing_required_field_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            FlagHistoryResponse(**kwargs)  # type: ignore[arg-type]