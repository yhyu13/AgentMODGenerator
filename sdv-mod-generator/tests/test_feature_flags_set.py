"""Tests for ``set_flag`` — the API-facing toggle wrapper.

Follow-up to the round-3 registry-helpers port and round-4
``get_pinned_flags`` port. Pins the contract ``set_flag`` documents:

- Unknown flag → returns ``None`` (route maps to 404).
- Known flag, value changed → returns the previous value, persists
  the new value, appends an audit entry to ``_history``.
- Known flag, no-op write (value unchanged) → returns the current
  value (which equals ``enabled``), still appends an audit entry.
- Pinned flag with drift → ``record_override`` raises
  ``FlagPinnedError``; ``set_flag`` does not catch.
- Pinned flag, no-op write → succeeds silently (the existing
  ``record_override`` pin guard is a no-drift guard, not a
  no-read guard).
- Non-bool ``enabled`` is coerced (route callers may pass
  ``1``/``0`` from JSON form-encoded data).

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
    set_flag,
    unpin_flag,
)


@pytest.fixture(autouse=True)
def _reset_flag_state():
    for attr in ("_overrides", "_history", "_locked_pins"):
        getattr(feature_flags, attr).clear()
    yield
    for attr in ("_overrides", "_history", "_locked_pins"):
        getattr(feature_flags, attr).clear()


def test_set_flag_unknown_returns_none():
    # A typo in the route must fail closed: no registry mutation,
    # no audit append, and the caller can map ``None`` to a 404.
    result = set_flag("nonsense_flag_xyz", True)
    assert result is None
    assert get_history("nonsense_flag_xyz") == []
    assert "nonsense_flag_xyz" not in feature_flags._overrides


def test_set_flag_known_returns_previous_value():
    # The contract: return the value that was in effect BEFORE
    # the mutation, so the route can echo "flipped from X to Y".
    # ``t2_three_judge_panel`` defaults to True, so the previous
    # value is True and the new value is False.
    result = set_flag("t2_three_judge_panel", False)
    assert result is True
    assert is_enabled("t2_three_judge_panel") is False


def test_set_flag_persists_new_value():
    # After the call, ``is_enabled`` must reflect the new value.
    set_flag("discord_dm_notifier", False)
    assert is_enabled("discord_dm_notifier") is False
    # A second call flips it back. Return is the now-current
    # False (the value being replaced).
    result = set_flag("discord_dm_notifier", True)
    assert result is False
    assert is_enabled("discord_dm_notifier") is True


def test_set_flag_appends_history_with_set_flag_reason():
    # The audit entry must be a ``FlagOverride`` with
    # ``reason="set_flag"`` and ``actor="system"`` — the
    # wrapper's stable identity in the audit log so operators
    # can distinguish API toggles from operator-pinned
    # overrides.
    set_flag("t2_three_judge_panel", False)
    events = get_history("t2_three_judge_panel")
    assert len(events) == 1
    event = events[0]
    assert event.name == "t2_three_judge_panel"
    assert event.value is False
    assert event.reason == "set_flag"
    assert event.actor == "system"


def test_set_flag_noop_returns_current_value():
    # A no-op write (enabled == current) still records an
    # audit entry (master's ``record_override`` does not have
    # a ``no_op`` parameter), and the return value equals the
    # value being "set" (which is the same as the current).
    # ``discord_dm_notifier`` defaults to True; setting True
    # again is a no-op and the return is True.
    result = set_flag("discord_dm_notifier", True)
    assert result is True
    assert is_enabled("discord_dm_notifier") is True
    # The audit log still got an entry.
    events = get_history("discord_dm_notifier")
    assert len(events) == 1
    assert events[0].value is True


def test_set_flag_pinned_raises_on_drift():
    # ``pin_flag`` locks the flag against drift. The pin guard
    # in :func:`record_override` is a "no-drift guard": it
    # fires only when the flag is pinned AND an override
    # already exists AND the new value differs. (A fresh
    # pinned flag with only a default value is *not* protected
    # by the guard — a known property of master's audit
    # shape, not something this wrapper changes.)
    #
    # To exercise the guard, first stage an override, then
    # pin the flag, then ``set_flag`` to a different value.
    # The wrapper routes through ``record_override``, which
    # raises ``FlagPinnedError``. The flag is not mutated and
    # no audit entry is appended (the raise happens inside
    # ``record_override`` before the append).
    record_override("t2_three_judge_panel", True, reason="seed", actor="test")
    pin_flag("t2_three_judge_panel")
    with pytest.raises(FlagPinnedError) as exc_info:
        set_flag("t2_three_judge_panel", False)
    assert exc_info.value.flag_name == "t2_three_judge_panel"
    # The seeded override is True, so the exception carries
    # the current value as True.
    assert exc_info.value.current_value is True
    # The flag is still at the seeded value (no drift).
    assert is_enabled("t2_three_judge_panel") is True
    # The rejected call did not append an entry beyond the
    # seed entry.
    events = get_history("t2_three_judge_panel")
    assert len(events) == 1
    assert events[0].reason == "seed"


def test_set_flag_pinned_noop_succeeds():
    # The pin guard is a "no-drift" guard, not a "no-read"
    # guard. A no-op write to a pinned flag (enabled == current)
    # must succeed silently: the existing ``record_override``
    # check is ``pinned AND in_overrides AND new != current``.
    # On a fresh process the flag is in ``_DEFAULT_FLAGS`` only
    # (not in ``_overrides``), so the guard's second condition
    # short-circuits to False and the override succeeds.
    # This matches the source's intent: a pinned flag cannot
    # drift, but reads and re-asserts of the current value
    # are fine.
    pin_flag("discord_dm_notifier")
    # Default is True; setting True is a no-op.
    result = set_flag("discord_dm_notifier", True)
    assert result is True
    assert is_enabled("discord_dm_notifier") is True
    # The audit log captured the call.
    events = get_history("discord_dm_notifier")
    assert len(events) == 1


def test_set_flag_coerces_enabled_to_bool():
    # JSON form-encoded data may deliver ``1`` / ``0`` (ints)
    # or ``"true"`` / ``"false"`` (strings) depending on the
    # client. ``bool()`` coercion turns all of them into a
    # proper ``True`` / ``False`` before the audit append, so
    # the history entry's ``value`` is always a real bool.
    # The strict ``bool`` signature on ``set_flag`` documents
    # the API contract; the test deliberately exercises the
    # runtime coercion behavior for non-bool callers that may
    # slip through (e.g. Pydantic v1 form-encoded inputs).
    result = set_flag("t2_three_judge_panel", 0)  # type: ignore[arg-type]
    assert result is True
    assert is_enabled("t2_three_judge_panel") is False
    events = get_history("t2_three_judge_panel")
    assert isinstance(events[0].value, bool)
    assert events[0].value is False

    result = set_flag("t2_three_judge_panel", 1)  # type: ignore[arg-type]
    assert result is False
    events = get_history("t2_three_judge_panel")
    assert events[0].value is True


def test_set_flag_accepts_staged_unknown_flag():
    # The wrapper treats a flag as "known" if it is in
    # ``_DEFAULT_FLAGS`` OR in ``_overrides`` (i.e. an
    # operator has already staged it via ``record_override``).
    # This is intentional: the registry allows operators to
    # stage flags before the code that gates on them lands,
    # and ``set_flag`` should be able to flip such a staged
    # flag's value via the same API used for registered flags.
    # The previous value is whatever the staging override set.
    record_override("future_flag", True, reason="staged", actor="alice")
    result = set_flag("future_flag", False)
    assert result is True
    assert is_enabled("future_flag") is False
    # And the audit log captured the wrapper's call.
    events = get_history("future_flag")
    assert len(events) == 2
    # Newest first: the ``set_flag`` entry is at index 0.
    assert events[0].reason == "set_flag"
    assert events[1].reason == "staged"


def test_set_flag_does_not_consume_pinned_state_after_rejection():
    # After a rejected ``set_flag`` on a pinned flag, the
    # pinned state must still be in effect. A subsequent
    # unpin + mutate must work; the rejection did not leave
    # any hidden state behind.
    #
    # Pre-seed an override so the pin guard fires (see the
    # comment in ``test_set_flag_pinned_raises_on_drift`` for
    # why the seed step is needed under master's guard
    # semantics).
    record_override("t2_three_judge_panel", True, reason="seed", actor="test")
    pin_flag("t2_three_judge_panel")
    with pytest.raises(FlagPinnedError):
        set_flag("t2_three_judge_panel", False)
    unpin_flag("t2_three_judge_panel")
    result = set_flag("t2_three_judge_panel", False)
    assert result is True
    assert is_enabled("t2_three_judge_panel") is False
