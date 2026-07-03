"""Tests for ``clear_flag_history`` — the audit-log reset helper.

Follow-up to the round-5 ``set_flag`` port and round-4 ``get_pinned_flags``
port. Mirrors ``test_feature_flags_pin.py``'s coverage of
``clear_pinned_flags``: the helper is test-only, its contract is
trivial, and a single regression in it could silently leak state
across tests (or worse, silently drop an operator's audit log in a
future refactor that swaps the storage shape).

What this file pins:

- ``clear_flag_history()`` empties ``_history`` so
  :func:`get_history` returns ``[]`` afterwards.
- It does NOT touch ``_overrides`` (the flag values are kept) or
  ``_locked_pins`` (the pin set is kept). Operators who want
  to wipe EVERYTHING need to call the three clears in sequence,
  or rely on the conftest autouse fixture.
- It is safe to call on an already-empty log (idempotent).
- It is safe to call repeatedly without state corruption.
- The helper does not raise, even when ``_history`` is fresh.

Hermetic: every test gets a reset fixture that clears
``_overrides``, ``_history``, and ``_locked_pins`` around the
test body, so test order cannot leak state into or out of these
cases. The fixture also exercises the new helper in-place so
the helper itself is part of the tested reset path.
"""
from __future__ import annotations

import pytest

from orchestrator import feature_flags
from orchestrator.feature_flags import (
    clear_flag_history,
    clear_pinned_flags,
    get_history,
    record_override,
)


@pytest.fixture(autouse=True)
def _reset_flag_state():
    """Wipe all module state around each test.

    Mirrors the round-5 conftest pattern: clear ``_overrides``
    and the audit log around every test body so a chatty
    upstream test (or an earlier round's leak) cannot pollute
    the cases below. The fixture uses the round-6
    ``clear_flag_history`` helper for the history leg so the
    helper itself is exercised on every test — a future
    refactor that breaks the helper would fail every case
    in this file at the fixture level, surfacing the
    regression loudly.
    """
    feature_flags._overrides.clear()
    clear_flag_history()
    clear_pinned_flags()
    yield
    feature_flags._overrides.clear()
    clear_flag_history()
    clear_pinned_flags()


def test_clear_flag_history_empties_audit_log():
    # The whole point: after ``clear_flag_history()`` the log is
    # empty and ``get_history()`` (with no filter) returns ``[]``.
    record_override("t2_three_judge_panel", False, reason="x", actor="a")
    record_override("discord_dm_notifier", True, reason="y", actor="b")
    assert len(get_history()) == 2
    clear_flag_history()
    assert get_history() == []


def test_clear_flag_history_does_not_touch_overrides():
    # The helper clears the audit log ONLY. The override map
    # must remain intact so a test that stages a flag and
    # then clears the history (a common pattern when
    # exercising read-only history queries) keeps the flag
    # in effect. Otherwise clearing the log would also
    # silently disable staged flags.
    record_override("t2_three_judge_panel", False, reason="x", actor="a")
    assert feature_flags._overrides == {"t2_three_judge_panel": False}
    clear_flag_history()
    assert feature_flags._overrides == {"t2_three_judge_panel": False}
    # And ``get_history`` with a name filter also returns []
    # — the log is empty for the staged flag too.
    assert get_history("t2_three_judge_panel") == []


def test_clear_flag_history_does_not_touch_pinned_set():
    # Symmetric contract with the override map: a cleared
    # audit log must not affect which flags are pinned.
    # Pinning is a separate operator-visible signal; conflating
    # the two would mean an operator who reads
    # ``GET /v1/feature_flag/pins`` after a routine
    # ``clear_flag_history()`` call (e.g. in a test) would see
    # their pinned flags disappear.
    record_override("t2_three_judge_panel", True, reason="x", actor="a")
    # Stage an override so the pin is well-formed (pin_flag
    # requires the flag to be in defaults or overrides).
    from orchestrator.feature_flags import pin_flag

    pin_flag("t2_three_judge_panel")
    assert "t2_three_judge_panel" in feature_flags._locked_pins
    clear_flag_history()
    assert "t2_three_judge_panel" in feature_flags._locked_pins


def test_clear_flag_history_is_idempotent_on_empty_log():
    # Calling the helper on a fresh log is a no-op: no
    # exception, no observable effect, no surprise return.
    # A regression that, say, raised ``IndexError`` on an
    # empty deque would surface here.
    assert get_history() == []
    clear_flag_history()
    assert get_history() == []


def test_clear_flag_history_is_idempotent_when_repeated():
    # Calling the helper twice in a row is a no-op on the
    # second call. This pins the contract that the helper
    # has no hidden "clear-once" flag or cursor state —
    # a future refactor that, for example, swaps ``deque.clear()``
    # for ``deque = deque()`` and forgets the rebind would
    # leak state into the next test and fail here.
    record_override("t2_three_judge_panel", False, reason="x", actor="a")
    record_override("discord_dm_notifier", True, reason="y", actor="b")
    clear_flag_history()
    clear_flag_history()
    clear_flag_history()
    assert get_history() == []


def test_clear_flag_history_preserves_subsequent_writes():
    # After the clear, new ``record_override`` calls append
    # to the log normally. A regression that, say, called
    # ``_history = deque()`` and orphaned the original
    # reference would surface here as a write that does
    # not appear in ``get_history()``.
    record_override("t2_three_judge_panel", False, reason="before", actor="a")
    clear_flag_history()
    record_override("t2_three_judge_panel", True, reason="after", actor="b")
    events = get_history("t2_three_judge_panel")
    assert len(events) == 1
    assert events[0].reason == "after"
