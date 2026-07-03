"""Tests for ``get_pinned_flags`` — the read-only pinned-set inspector.

Follow-up to the round-3 registry-helpers port. Mirrors the
``test_feature_flags_registry.py`` autouse-fixture pattern: hermetic,
clears ``_locked_pins`` around every test, and asserts the contract
``get_pinned_flags`` documents — a fresh sorted tuple per call that
is decoupled from the underlying set mutation.
"""
from __future__ import annotations

import pytest

from orchestrator import feature_flags
from orchestrator.feature_flags import (
    get_pinned_flags,
    pin_flag,
    unpin_flag,
)


@pytest.fixture(autouse=True)
def _reset_pinned_set():
    feature_flags._locked_pins.clear()
    yield
    feature_flags._locked_pins.clear()


def test_get_pinned_flags_is_empty_by_default():
    # A fresh process (or a freshly-cleared test) has no pins.
    assert get_pinned_flags() == ()


def test_get_pinned_flags_returns_sorted_tuple():
    # Pin two flags in non-alphabetical order; the result must be
    # sorted ascending so snapshot tests are deterministic.
    pin_flag("t2_three_judge_panel")
    pin_flag("discord_dm_notifier")
    assert get_pinned_flags() == (
        "discord_dm_notifier",
        "t2_three_judge_panel",
    )
    # And the result type is a tuple, not a list or view.
    assert isinstance(get_pinned_flags(), tuple)


def test_get_pinned_flags_does_not_mutate_underlying_set():
    # The helper returns a fresh tuple per call. Mutating the
    # returned tuple must not affect the underlying set, and a
    # second call must reflect the next state of the set, not a
    # cached snapshot.
    pin_flag("security_headers_middleware")
    first = get_pinned_flags()
    assert first == ("security_headers_middleware",)
    # Pin a second flag and re-query.
    pin_flag("t2_three_judge_panel")
    second = get_pinned_flags()
    assert second == (
        "security_headers_middleware",
        "t2_three_judge_panel",
    )


def test_get_pinned_flags_reflects_unpin():
    # ``unpin_flag`` removes from the set; the inspector must
    # observe the removal on the next call.
    pin_flag("t2_three_judge_panel")
    pin_flag("discord_dm_notifier")
    assert get_pinned_flags() == (
        "discord_dm_notifier",
        "t2_three_judge_panel",
    )
    unpin_flag("t2_three_judge_panel")
    assert get_pinned_flags() == ("discord_dm_notifier",)
    unpin_flag("discord_dm_notifier")
    assert get_pinned_flags() == ()


def test_get_pinned_flags_unknown_pin_does_not_pollute_set():
    # ``pin_flag`` returns ``None`` for an unknown flag (so the
    # operator endpoint can 404) and the set must not contain
    # the unknown name. This pins the contract that the inspector
    # only ever returns names that were validly pinned.
    assert pin_flag("nonexistent_rollout_flag") is None
    assert "nonexistent_rollout_flag" not in get_pinned_flags()
    assert get_pinned_flags() == ()
