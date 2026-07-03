"""Tests for the registry-inspection helpers of orchestrator/feature_flags.

Follow-up to the round 2 pin/unpin port. The two helpers under test
(``known_flags`` and ``utcnow_iso_z``) are pulled from
``docs/_source_feature_flags.py.txt`` lines 99-123 — pure utilities
that don't touch the existing ``_overrides`` / ``_history`` /
``_locked_pins`` state. Pinning their behavior here so a future
refactor can't quietly change the sorted-iteration contract or the
ISO-8601 ``Z``-suffix format that operator endpoints will rely on.
"""
from __future__ import annotations

import re

from orchestrator import feature_flags
from orchestrator.feature_flags import (
    known_flags,
    utcnow_iso_z,
)


def test_known_flags_returns_sorted_tuple_of_defaults():
    # The three default flags shipped in the cleanroom port, sorted.
    # We don't pin the count (the registry may grow) — only the
    # ordering and the inclusion of the known defaults.
    flags = known_flags()
    assert isinstance(flags, tuple)
    assert flags == tuple(sorted(flags))
    assert "t2_three_judge_panel" in flags
    assert "discord_dm_notifier" in flags
    assert "security_headers_middleware" in flags


def test_known_flags_does_not_include_unregistered_overrides():
    # ``record_override`` accepts unknown names (staging a flag
    # before code lands), but ``known_flags`` only reflects the
    # canonical registry. Otherwise the operator endpoint would
    # surface stale future-flag names forever.
    feature_flags._overrides["future_flag_xyz"] = True
    try:
        assert "future_flag_xyz" not in known_flags()
    finally:
        feature_flags._overrides.clear()


def test_utcnow_iso_z_ends_with_z_suffix():
    # The ``Z`` suffix is the whole point — Python's default
    # ``isoformat()`` emits ``+00:00`` which is parseable but
    # harder to grep and not the format dashboards expect.
    stamp = utcnow_iso_z()
    assert stamp.endswith("Z")
    assert "+00:00" not in stamp


def test_utcnow_iso_z_format_is_iso_8601_with_microseconds():
    # Match ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` — microsecond
    # precision, not the default second precision.
    pattern = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
    )
    assert pattern.match(utcnow_iso_z()), (
        f"timestamp did not match the expected ISO-8601 microsecond format: "
        f"{utcnow_iso_z()!r}"
    )


def test_utcnow_iso_z_is_monotonic_across_calls():
    # Two consecutive calls must not produce a timestamp that
    # goes backwards. A flake here would mean the helper is
    # reaching into non-monotonic time sources (which it
    # shouldn't — but a future refactor that, say, calls
    # ``time.time()`` instead of ``datetime.now()`` could break
    # this).
    first = utcnow_iso_z()
    second = utcnow_iso_z()
    assert first <= second
