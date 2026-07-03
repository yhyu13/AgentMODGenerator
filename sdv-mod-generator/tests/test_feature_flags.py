"""Tests for orchestrator/feature_flags.

Covers the cleanroom port (orchestrator/feature_flags.py) shipped at
7f1b205 — the four self-contained helpers ``is_enabled``,
``record_override``, ``list_pins``, ``get_history``, plus the
``FlagOverride`` dataclass.

The module holds process-local state (a module-level ``_overrides``
dict and ``_history`` deque), so every test gets a reset fixture that
clears both before and after the test body. Without this, the test
order would be load-bearing.

Why test the cleanroom port rather than the full branch version:
the full branch file is 566 lines and depends on the rest of the
P3-P5 rollout stack (admin endpoints, Redis-backed rollout
percentages, audit log persistence) which is not on master. The
cleanroom port is what master has; this file pins its behavior.
"""
from __future__ import annotations

import pytest

from orchestrator import feature_flags
from orchestrator.feature_flags import (
    FlagOverride,
    clear_flag_history,
    get_history,
    is_enabled,
    list_pins,
    record_override,
)


@pytest.fixture(autouse=True)
def _reset_flag_state():
    """Clear module-level state around each test.

    The cleanroom port stores overrides and history in module
    globals (``_overrides`` and ``_history``). Test order would
    otherwise leak state across cases — pin a flag in one test and
    the next test's ``is_enabled`` call would see the pin. The
    round-6 ``clear_flag_history`` helper consolidates the audit
    log reset so future refactors that change the history
    storage shape only need to touch one function.
    """
    feature_flags._overrides.clear()
    clear_flag_history()
    yield
    feature_flags._overrides.clear()
    clear_flag_history()


class TestIsEnabled:
    def test_known_default_true(self):
        # ``t2_three_judge_panel`` is the one call site already
        # wired up in quality/gate_t2.py.
        assert is_enabled("t2_three_judge_panel") is True

    def test_known_default_true_others(self):
        assert is_enabled("discord_dm_notifier") is True
        assert is_enabled("security_headers_middleware") is True

    def test_unknown_flag_returns_false(self):
        # Deny-by-default: a typo in a gate call must fail closed
        # rather than silently enabling a feature.
        assert is_enabled("nonsense_flag_xyz") is False

    def test_override_beats_default(self):
        record_override("t2_three_judge_panel", False, reason="test", actor="unit")
        assert is_enabled("t2_three_judge_panel") is False

    def test_override_can_re_enable(self):
        record_override("t2_three_judge_panel", False, reason="off")
        record_override("t2_three_judge_panel", True, reason="on")
        assert is_enabled("t2_three_judge_panel") is True


class TestRecordOverride:
    def test_records_history_event(self):
        record_override(
            "t2_three_judge_panel",
            False,
            reason="temporarily disable",
            actor="alice",
        )
        events = get_history("t2_three_judge_panel")
        assert len(events) == 1
        event = events[0]
        assert event.name == "t2_three_judge_panel"
        assert event.value is False
        assert event.reason == "temporarily disable"
        assert event.actor == "alice"

    def test_default_actor_is_system(self):
        record_override("t2_three_judge_panel", True)
        event = get_history("t2_three_judge_panel")[0]
        assert event.actor == "system"

    def test_default_reason_is_empty(self):
        record_override("t2_three_judge_panel", True, actor="bob")
        event = get_history("t2_three_judge_panel")[0]
        assert event.reason == ""

    def test_unknown_flag_is_accepted(self):
        # Operators can stage a flag before code lands. The pin
        # is stored even though ``is_enabled`` will still return
        # False for it (deny-by-default wins on lookup).
        record_override("future_flag", True, actor="alice")
        assert is_enabled("future_flag") is True
        events = get_history("future_flag")
        assert len(events) == 1


class TestListPins:
    def test_empty_initially(self):
        assert list_pins() == {}

    def test_returns_current_pins(self):
        record_override("t2_three_judge_panel", False, reason="x", actor="a")
        record_override("discord_dm_notifier", True, reason="y", actor="b")
        pins = list_pins()
        assert pins == {
            "t2_three_judge_panel": False,
            "discord_dm_notifier": True,
        }

    def test_returned_dict_is_a_copy(self):
        record_override("t2_three_judge_panel", True, reason="x", actor="a")
        pins = list_pins()
        pins["t2_three_judge_panel"] = False
        # Mutating the returned dict must not affect the module
        # state — operators reading the pin set shouldn't be able
        # to silently flip flags.
        assert is_enabled("t2_three_judge_panel") is True


class TestGetHistory:
    def test_empty_initially(self):
        assert get_history() == []

    def test_newest_first(self):
        record_override("t2_three_judge_panel", True, reason="first", actor="a")
        record_override("t2_three_judge_panel", False, reason="second", actor="b")
        events = get_history("t2_three_judge_panel")
        assert [e.reason for e in events] == ["second", "first"]

    def test_filter_by_name(self):
        record_override("t2_three_judge_panel", True, reason="x", actor="a")
        record_override("discord_dm_notifier", False, reason="y", actor="b")
        events = get_history("discord_dm_notifier")
        assert len(events) == 1
        assert events[0].name == "discord_dm_notifier"

    def test_filter_by_name_returns_empty_when_no_match(self):
        record_override("t2_three_judge_panel", True, reason="x", actor="a")
        assert get_history("discord_dm_notifier") == []

    def test_history_is_capped(self):
        # The deque is bounded so a chatty operator can't OOM the
        # process. Write past the cap and confirm the older events
        # are dropped.
        cap = feature_flags._HISTORY_LIMIT
        for i in range(cap + 5):
            record_override("t2_three_judge_panel", i % 2 == 0, reason=str(i), actor="a")
        events = get_history("t2_three_judge_panel")
        assert len(events) == cap
        # The most recent write must still be present.
        assert events[0].reason == str(cap + 5 - 1)


class TestFlagOverride:
    def test_is_frozen(self):
        event = FlagOverride(name="x", value=True, reason="r", actor="a")
        with pytest.raises(Exception):
            # Frozen dataclass — assignment must raise. The exact
            # exception type is ``dataclasses.FrozenInstanceError``
            # on CPython, but the broad ``Exception`` keeps the
            # test robust to dataclass-as-dataclass replacements.
            event.name = "y"  # type: ignore[misc]

    def test_fields_are_accessible(self):
        event = FlagOverride(name="t2", value=True, reason="r", actor="alice")
        assert event.name == "t2"
        assert event.value is True
        assert event.reason == "r"
        assert event.actor == "alice"
