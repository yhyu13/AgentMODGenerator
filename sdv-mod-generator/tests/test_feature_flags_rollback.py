"""Tests for ``rollback_flag`` — the audit-log reverse-scan helper.

Follow-up to the round-5 ``set_flag`` port. Pins the contract
``rollback_flag`` documents:

- Unknown flag → returns ``None`` (route maps to 404).
- Known flag, no audit history for the flag → returns ``None``
  (route maps to 409 Conflict — flag exists but nothing to undo).
- Known flag with one matching entry → restores the registry
  default (the value before the first change).
- Known flag with two+ matching entries → restores the value of
  the second-most-recent entry (the value before the most recent
  change).
- The companion ``set_flag`` write is recorded in
  ``_history`` (reason="set_flag", actor="system") so the
  rollback itself is visible in ``get_history``.
- Pinned flag with a non-current rollback target → ``set_flag``
  raises ``FlagPinnedError``; ``rollback_flag`` does not catch.
- The response dict shape matches the source's contract:
  ``name``, ``rolled_back_from``, ``rolled_back_to``,
  ``restored_entry_index``, ``history_size_at_rollback``.

Hermetic: every test clears ``_overrides``, ``_history``, and
``_locked_pins`` around its body so test order cannot leak state.
"""
from __future__ import annotations

import pytest

from orchestrator import feature_flags
from orchestrator.feature_flags import (
    FlagPinnedError,
    get_history,
    is_enabled,
    pin_flag,
    record_override,
    rollback_flag,
    set_flag,
)


@pytest.fixture(autouse=True)
def _reset_flag_state():
    for attr in ("_overrides", "_history", "_locked_pins"):
        getattr(feature_flags, attr).clear()
    yield
    for attr in ("_overrides", "_history", "_locked_pins"):
        getattr(feature_flags, attr).clear()


def test_rollback_flag_unknown_returns_none():
    # A typo in the route must fail closed: no registry mutation,
    # no audit append, and the caller can map ``None`` to a 404.
    result = rollback_flag("nonsense_flag_xyz")
    assert result is None
    assert get_history("nonsense_flag_xyz") == []
    assert "nonsense_flag_xyz" not in feature_flags._overrides


def test_rollback_flag_known_no_history_returns_none():
    # The flag is in the registry (default-True) but the audit
    # log has no rows for it. The route maps this to 409
    # Conflict, distinct from 404. The default value is left
    # untouched (the function returns before any mutation).
    result = rollback_flag("t2_three_judge_panel")
    assert result is None
    # Default is still True — no mutation attempted.
    assert is_enabled("t2_three_judge_panel") is True


def test_rollback_flag_single_entry_restores_default():
    # One matching entry: the most recent change set the flag
    # to ``False``. The pre-mutation value is the registry
    # default (``True``). Rollback should restore ``True``.
    record_override("t2_three_judge_panel", False, reason="ops", actor="alice")
    result = rollback_flag("t2_three_judge_panel")
    assert result is not None
    assert result["name"] == "t2_three_judge_panel"
    assert result["rolled_back_from"] is False
    assert result["rolled_back_to"] is True
    # The flag is back at the registry default.
    assert is_enabled("t2_three_judge_panel") is True
    # The restored_entry_index is the absolute index of the
    # matched entry in the newest-first list. The single
    # entry sits at index 0.
    assert result["restored_entry_index"] == 0
    # The companion ``set_flag`` write appended one new entry
    # — size is 1 (rollback) + 1 (original) = 2.
    assert result["history_size_at_rollback"] == 2
    # And the audit log is newest-first; the rollback entry
    # is at the head.
    events = get_history("t2_three_judge_panel")
    assert len(events) == 2
    assert events[0].reason == "set_flag"
    assert events[0].value is True
    assert events[0].actor == "system"
    assert events[1].reason == "ops"


def test_rollback_flag_multiple_entries_restores_prior_value():
    # Two matching entries: most recent set the flag to
    # ``False`` (after a prior ``True``). The pre-mutation
    # value of the most recent change is the value of the
    # second-most-recent entry (``True``). Rollback should
    # restore ``True``.
    record_override("discord_dm_notifier", True, reason="seed", actor="alice")
    record_override("discord_dm_notifier", False, reason="disable", actor="bob")
    result = rollback_flag("discord_dm_notifier")
    assert result is not None
    assert result["rolled_back_from"] is False
    assert result["rolled_back_to"] is True
    assert is_enabled("discord_dm_notifier") is True
    # The most recent matching entry (False / "disable") sits
    # at index 0 in the newest-first log.
    assert result["restored_entry_index"] == 0
    # Three entries total: seed, disable, rollback-write.
    assert result["history_size_at_rollback"] == 3
    events = get_history("discord_dm_notifier")
    assert events[0].reason == "set_flag"
    assert events[0].value is True
    assert events[1].reason == "disable"
    assert events[1].value is False
    assert events[2].reason == "seed"
    assert events[2].value is True


def test_rollback_flag_ignores_other_flags_entries():
    # The scan window contains entries for OTHER flags. The
    # helper must filter to ``name`` only and not be confused
    # by inter-flag interleaving. Three entries for
    # ``t2_three_judge_panel`` interleaved with two for
    # ``discord_dm_notifier`` — rolling back ``t2`` should
    # restore the value of the second-most-recent ``t2``
    # entry, not the value of any ``discord_dm_notifier``
    # entry.
    record_override("t2_three_judge_panel", True, reason="t2-on", actor="alice")
    record_override("discord_dm_notifier", False, reason="dm-off", actor="alice")
    record_override("t2_three_judge_panel", False, reason="t2-off", actor="bob")
    record_override("discord_dm_notifier", True, reason="dm-on", actor="bob")
    record_override("t2_three_judge_panel", True, reason="t2-on-2", actor="carol")
    result = rollback_flag("t2_three_judge_panel")
    assert result is not None
    # The most recent ``t2`` change was ``True`` (t2-on-2);
    # the pre-mutation value is the prior ``t2`` entry
    # (``False``, t2-off). The interleaved ``dm`` entries
    # must NOT affect the result.
    assert result["rolled_back_from"] is True
    assert result["rolled_back_to"] is False
    # ``dm`` was not touched.
    assert is_enabled("discord_dm_notifier") is True
    # The matched ``t2`` entry sits at index 0 in the
    # newest-first log (the others are at 2 and 4).
    assert result["restored_entry_index"] == 0


def test_rollback_flag_pinned_raises_on_drift():
    # Pinned flag with a non-current rollback target: the
    # helper must propagate ``FlagPinnedError`` so the API
    # layer can return 423 Locked. The flag is left
    # untouched (no rollback, no audit append beyond the
    # pre-pinned state).
    #
    # The pin guard condition in ``record_override`` is
    # ``pinned AND in_overrides AND new != current``. The
    # companion ``set_flag`` write inside ``rollback_flag``
    # passes ``new = target_previous_value``. So the raise
    # fires when ``target_previous_value != is_enabled(name)``.
    # If only one matching entry exists, the target is the
    # registry default. For a default-True flag with one
    # entry setting it to False, the target is True and
    # the current is False → drift, raise.
    record_override("t2_three_judge_panel", False, reason="off", actor="alice")
    pin_flag("t2_three_judge_panel")
    with pytest.raises(FlagPinnedError) as exc_info:
        rollback_flag("t2_three_judge_panel")
    assert exc_info.value.flag_name == "t2_three_judge_panel"
    # The flag is still at the most recent value (False) —
    # the rollback did not succeed.
    assert is_enabled("t2_three_judge_panel") is False
    # No rollback-entry was appended (the audit log is still
    # at 1: the original "off" record).
    events = get_history("t2_three_judge_panel")
    assert len(events) == 1
    assert events[0].reason == "off"


def test_rollback_flag_response_dict_shape():
    # The source's contract returns a flat dict with exactly
    # 5 keys. The API layer can ``**``-unpack into a
    # Pydantic ``FeatureFlagRollbackResponse``. Lock the
    # key set so a future refactor that drops a key (e.g.
    # ``restored_entry_index``) is caught.
    record_override("t2_three_judge_panel", False, reason="seed", actor="alice")
    result = rollback_flag("t2_three_judge_panel")
    assert result is not None
    assert set(result.keys()) == {
        "name",
        "rolled_back_from",
        "rolled_back_to",
        "restored_entry_index",
        "history_size_at_rollback",
    }
