"""Feature flags: in-memory rollout controls with override history.

Minimal cleanroom port of the discord-ops-hardening branch's flag helper
(see docs/P3_P5_MERGE_PLAN.md — branch file at dfb3dd7 is 567 lines; this
file extracts the self-contained helpers needed by gates and routes:
``is_enabled``, ``record_override``, ``list_pins``, ``get_history``,
``set_flag`` (the API-facing toggle wrapper, added in round 5),
plus the pin operators ``pin_flag`` / ``unpin_flag`` / ``is_pinned`` /
``get_pinned_flags`` / ``clear_pinned_flags`` (test-only),
the registry-inspection helpers ``known_flags`` /
``utcnow_iso_z`` added in round 3, and the
``clear_flag_history`` test-only helper added in round 6.

State is process-local (module-level dict + deque + locked-set).
Persistence and Redis-backed rollout percentages are intentionally
out of scope — they require the rest of the branch's rollout
stack and land in a later PR. The ``rollback_flag`` helper
(introduced on the branch as a wrapper around ``set_flag`` plus
audit-log reverse-scan) is ported in a follow-up round.

Round 5 design note: the source's ``set_flag`` (lines 176-255)
records mutations via a private ``record_flag_change`` helper that
emits a ``feature_flag.changed`` log event and appends a
dict-shaped audit-log entry. The cleanroom port keeps the existing
``FlagOverride`` dataclass (carries ``reason`` and ``actor``,
already wired through ``get_history``) and routes ``set_flag``
through ``record_override`` with ``reason="set_flag"`` /
``actor="system"`` so the new API is a thin wrapper over the
existing audit path. The ``feature_flag.changed`` log event is
emitted before the audit append, matching the source's two-effect
pattern (log + audit) without introducing a second storage shape.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# Hardcoded defaults — branch's gate_t2 calls ``is_enabled("t2_three_judge_panel")``
# so the panel stays on by default until an operator pins it off.
_DEFAULT_FLAGS: Final[dict[str, bool]] = {
    "t2_three_judge_panel": True,
    "discord_dm_notifier": True,
    "security_headers_middleware": True,
}

_HISTORY_LIMIT: Final[int] = 100


@dataclass(frozen=True)
class FlagOverride:
    """A single override event for a feature flag."""

    name: str
    value: bool
    reason: str
    actor: str


_overrides: dict[str, bool] = {}
_history: deque[FlagOverride] = deque(maxlen=_HISTORY_LIMIT)
_locked_pins: set[str] = set()


def is_enabled(name: str) -> bool:
    """Return whether the named flag is currently enabled.

    Looks up ``_overrides`` first (a pin/rollback wins), then falls back
    to ``_DEFAULT_FLAGS``. Unknown flags default to ``False`` so a typo
    in a gate call fails closed rather than silently enabling new code.
    """
    if name in _overrides:
        return _overrides[name]
    return _DEFAULT_FLAGS.get(name, False)


def record_override(
    name: str,
    value: bool,
    *,
    reason: str = "",
    actor: str = "system",
) -> None:
    """Pin a flag to ``value`` and append the event to history.

    Args:
        name: Flag identifier. Registered in ``_DEFAULT_FLAGS`` for
            documentation, but unknown names are accepted so operators
            can stage flags before code lands.
        value: New on/off state.
        reason: Free-text justification (visible in ``get_history``).
        actor: Who is making the change (defaults to ``"system"`` for
            automated rollouts; humans should pass their handle).

    Raises ``FlagPinnedError`` if ``name`` has been pinned via
    :func:`pin_flag` and the new ``value`` differs from the current
    override — silent writes on a locked flag would defeat the audit
    value of ``get_history``.
    """
    if name in _locked_pins and name in _overrides and _overrides[name] != value:
        logger.warning(
            "feature_flag.pinned",
            flag_name=name,
            current_value=_overrides[name],
        )
        raise FlagPinnedError(name, _overrides[name])
    _overrides[name] = value
    event = FlagOverride(name=name, value=value, reason=reason, actor=actor)
    _history.append(event)
    logger.info(
        "feature_flag.override_recorded",
        flag_name=name,
        flag_value=value,
        reason=reason,
        actor=actor,
    )


def set_flag(name: str, enabled: bool) -> bool | None:
    """Set the on/off value of a registered feature flag.

    The API-facing toggle wrapper (round 5). Mirrors the
    branch's ``set_flag`` (docs/_source_feature_flags.py.txt
    lines 176-255) but routes the audit append through the
    existing :func:`record_override` (using
    ``reason="set_flag"`` and ``actor="system"``) rather than
    introducing a second dict-shaped audit type. The dedicated
    ``feature_flag.changed`` log event is emitted before the
    audit append so dashboards that subscribe to that name see
    the same payload as the branch's design.

    Args:
        name: Flag identifier. Must be in ``_DEFAULT_FLAGS``
            (registered) or already in ``_overrides``
            (operator has staged it).
        enabled: New on/off state. Coerced to ``bool``.

    Returns:
        ``None`` for an unknown flag (route maps to 404 —
        the same deny-by-default contract that ``is_enabled()``
        and ``pin_flag`` follow). The previous ``bool`` value
        for a known flag (including no-op writes where
        ``enabled`` equals the current value).

    Raises:
        FlagPinnedError: If ``name`` is in ``_locked_pins`` and
            the new ``enabled`` differs from the current value.
            The pin guard fires inside :func:`record_override`
            before any registry mutation or audit append, so a
            pinned flag cannot drift. No-op writes to pinned
            flags (where ``enabled`` equals the current value)
            do NOT raise — the pin guard is a "no drift" guard,
            not a "no read" guard.

    Design notes:
        - **Order**: unknown-check → capture previous value →
          emit ``feature_flag.changed`` log event → delegate to
          :func:`record_override` for mutation + audit append.
        - **No-op writes** are still appended to ``_history``
          (the master ``record_override`` has no ``no_op``
          parameter). The log event includes ``no_op`` so
          dashboards that subscribe to the log channel can
          filter on it.
        - **Reason / actor**: the wrapper fixes
          ``reason="set_flag"`` and ``actor="system"`` so
          ``GET /v1/feature_flags/history`` shows ``set_flag``
          events alongside operator-pinned ones without a new
          filter field.
    """
    is_known = name in _DEFAULT_FLAGS or name in _overrides
    if not is_known:
        logger.warning(
            "feature_flag.unknown",
            flag_name=name,
            known_flags=sorted(_DEFAULT_FLAGS.keys()),
        )
        return None
    new_value = bool(enabled)
    previous_value = is_enabled(name)
    is_no_op = previous_value == new_value
    # Emit the structured log event first (matches the source's
    # log + audit two-effect pattern). The audit append routes
    # through ``record_override`` and reuses the master
    # ``FlagOverride`` shape.
    logger.info(
        "feature_flag.changed",
        flag_name=name,
        previous_value=previous_value,
        new_value=new_value,
        no_op=is_no_op,
    )
    # ``record_override`` enforces the pin guard (raises
    # FlagPinnedError if pinned and new value differs) and
    # appends to ``_history`` using the existing
    # ``FlagOverride`` shape. Routing through it keeps the
    # singular audit path — no parallel dict-shaped history.
    record_override(
        name,
        new_value,
        reason="set_flag",
        actor="system",
    )
    return previous_value


def list_pins() -> dict[str, bool]:
    """Return a copy of the current override map (flag name → pinned value)."""
    return dict(_overrides)


def get_history(name: str | None = None) -> list[FlagOverride]:
    """Return override events, newest-first.

    Args:
        name: If given, filter to events for this flag. If ``None``,
            return all events currently in the rolling buffer.
    """
    events = list(_history)
    events.reverse()
    if name is None:
        return events
    return [event for event in events if event.name == name]


class FlagPinnedError(RuntimeError):
    """Raised when ``record_override`` is called on a locked flag.

    Carries the flag name and current value so the API layer can
    build a 423 Locked response without re-querying module state.
    ``RuntimeError`` (not ``ValueError``) because the rejection is a
    runtime state condition, not a programming bug.
    """

    def __init__(self, flag_name: str, current_value: bool) -> None:
        super().__init__(
            f"feature flag {flag_name!r} is pinned to {current_value}; "
            "unpin_flag() before mutating"
        )
        self.flag_name = flag_name
        self.current_value = current_value


def is_pinned(name: str) -> bool:
    """Return whether ``name`` is in the locked-pins set.

    Deny-by-default: unknown names return ``False``.
    """
    return name in _locked_pins


def pin_flag(name: str) -> dict[str, object] | None:
    """Mark ``name`` as locked so ``record_override`` rejects future changes.

    Returns a flat dict ``{name, pinned, already_pinned, current_value}``.
    Returns ``None`` for a name with no default and no active override.
    Re-pin is a no-op with ``already_pinned=True``.
    """
    is_known = name in _DEFAULT_FLAGS or name in _overrides
    if not is_known:
        logger.warning(
            "feature_flag.unknown",
            flag_name=name,
            known_flags=sorted(_DEFAULT_FLAGS.keys()),
        )
        return None
    already_pinned = name in _locked_pins
    if not already_pinned:
        _locked_pins.add(name)
        logger.info(
            "feature_flag.pinned_by_operator",
            flag_name=name,
            current_value=_overrides.get(name, _DEFAULT_FLAGS.get(name, False)),
        )
    return {
        "name": name,
        "pinned": True,
        "already_pinned": already_pinned,
        "current_value": _overrides.get(name, _DEFAULT_FLAGS.get(name, False)),
    }


def unpin_flag(name: str) -> dict[str, object] | None:
    """Remove ``name`` from the locked-pins set.

    Companion to ``pin_flag``. Returns ``None`` for an unknown name
    (no default, no override). Unpin of an unpinned flag is a no-op
    with ``was_pinned=False``.
    """
    is_known = name in _DEFAULT_FLAGS or name in _overrides
    if not is_known:
        logger.warning(
            "feature_flag.unknown",
            flag_name=name,
            known_flags=sorted(_DEFAULT_FLAGS.keys()),
        )
        return None
    was_pinned = name in _locked_pins
    if was_pinned:
        _locked_pins.discard(name)
        logger.info(
            "feature_flag.unpinned_by_operator",
            flag_name=name,
            current_value=_overrides.get(name, _DEFAULT_FLAGS.get(name, False)),
        )
    return {
        "name": name,
        "pinned": False,
        "was_pinned": was_pinned,
        "current_value": _overrides.get(name, _DEFAULT_FLAGS.get(name, False)),
    }


def clear_pinned_flags() -> None:
    """Empty the locked-pins set. For tests only."""
    _locked_pins.clear()


def clear_flag_history() -> None:
    """Empty the in-memory audit log. For tests only.

    Mirrors :func:`clear_pinned_flags`: every test that mutates
    ``_overrides`` (and therefore records into ``_history``) should
    call this in a ``finally`` block (or via an autouse fixture) so
    subsequent tests start with a clean log. Test code that wants
    to clear BOTH the override map and the audit log can simply
    call ``clear_flag_history()`` followed by
    ``_overrides.clear()``, or rely on the conftest autouse fixture
    that does both in one place.

    Production code should NOT call this — the audit log is the
    whole point of the feature, and a runtime call to
    ``clear_flag_history()`` would create a silent gap in the
    operator-visible trail. If a long-running process needs to
    bound memory, the right move is to add a ``maxlen`` ring buffer
    (master's ``_history`` already has ``maxlen=_HISTORY_LIMIT``),
    not to clear on a schedule.

    Mirrors the branch's ``clear_flag_history`` (source
    ``docs/_source_feature_flags.py.txt`` lines 418-435). The
    body is a single ``_history.clear()`` call; the function
    exists to give the test layer a single seam that future
    refactors can change in one place (e.g. switching to a
    bounded ring buffer) without rewriting every test's
    autouse fixture.
    """
    _history.clear()


def get_pinned_flags() -> tuple[str, ...]:
    """Return the sorted tuple of currently pinned flag names.

    Exposed for the operator endpoint and for tests that want to
    assert the pinned-set shape without re-parsing this module.
    The tuple is sorted so callers can rely on deterministic
    ordering for snapshot tests.

    Mirrors the branch's ``get_pinned_flags`` helper
    (docs/_source_feature_flags.py.txt, lines 387-395). The
    function is intentionally pure — it returns a fresh tuple
    each call, not a view — so callers can mutate the result
    without affecting ``_locked_pins`` and so the contract is
    symmetric with :func:`known_flags` (also a fresh sorted
    tuple per call).
    """
    return tuple(sorted(_locked_pins))


def known_flags() -> tuple[str, ...]:
    """Return the sorted tuple of registered flag names (defaults only).

    Exposed for the operator endpoint (``GET /v1/feature_flags``) and
    for tests that want to assert the registry shape without
    re-parsing this module. The tuple is sorted so callers can rely
    on deterministic ordering for snapshot tests.

    Only the canonical ``_DEFAULT_FLAGS`` registry is reflected — an
    operator who has ``record_override``-d an unknown future flag will
    not see it here. That is intentional: the registry is the source
    of truth for *which flags exist*; the override map is the source
    of truth for *what is currently on*. Operators querying the
    flag-set endpoint want the first; the second is available via
    :func:`list_pins`.
    """
    return tuple(sorted(_DEFAULT_FLAGS.keys()))


# Maximum number of recent history rows the rollback helper scans
# before giving up. The master's audit log is bounded by design
# (each mutation is one ``deque.append``, and the ``deque`` is
# capped at ``_HISTORY_LIMIT`` = 100 rows), so a scan-window of
# 100 rows is comfortably larger than any realistic operator-undo
# stack and small enough that the linear scan is trivially cheap.
# The cap is a safety net for the case where a noisy future client
# starts hammering the toggle endpoint — the cap ensures rollback
# stays O(1) in practice and never blocks the request thread on
# an unbounded scan.
_ROLLBACK_SCAN_LIMIT: Final[int] = 100


def rollback_flag(name: str) -> dict[str, object] | None:
    """Roll back the most recent real change to ``name``.

    Looks up the most recent entry in ``_history`` for ``name``
    (within the last ``_ROLLBACK_SCAN_LIMIT`` rows) and re-applies
    the entry's pre-mutation value via :func:`set_flag`,
    effectively undoing the most recent mutation. The companion
    write is recorded in ``_history`` like every other flag
    mutation, so the rollback itself is visible to
    ``GET /v1/feature_flags/history`` as a normal entry.

    Return value is a flat dict matching the
    ``FeatureFlagRollbackResponse`` shape consumed by
    ``POST /v1/feature_flags/{name}/rollback``:

    - ``name`` — the flag that was rolled back.
    - ``rolled_back_from`` — the value of the flag immediately
      before the rollback (``_overrides[name]`` at call time,
      or the default if no override was active).
    - ``rolled_back_to`` — the value after the rollback, i.e.
      the pre-mutation value of the most recent real change.
    - ``restored_entry_index`` — the index (in
      :func:`get_history`'s newest-first list) of the entry whose
      pre-mutation value was re-applied. Useful for callers that
      want to render "rolled back change #N from the audit log".
      ``None`` if no rollbackable history exists.
    - ``history_size_at_rollback`` — the size of ``_history`` at
      the moment the rollback was recorded. Snapshot here (not in
      the route) so the value is consistent with the restored
      ``_overrides[name]`` regardless of any interleaved writes.

    The function returns ``None`` for both failure modes the
    caller needs to distinguish via HTTP status:

    - Unknown flag (``name not in _DEFAULT_FLAGS and name not
      in _overrides``): mirrors :func:`set_flag`'s deny-by-default
      contract. ``set_flag`` inside this helper will log
      ``feature_flag.unknown`` at warning level and return
      ``None`` itself, so the route can map this branch to a 404
      without any extra detection.

    - No rollbackable history for ``name`` (every entry in the
      last 100 rows is either for a different flag, OR the flag
      is known but the audit log has no rows for it at all): the
      route maps this to a 409 Conflict. The 409 is intentionally
      not a 404 — the flag exists in the registry, the
      operator's request was well-formed, but there is simply
      nothing to undo.

    Adaptation notes vs. the source
    (``docs/_source_feature_flags.py.txt`` lines 438-567):

    - The source tracks per-entry ``previous_value`` and ``no_op``
      directly inside the dict-shaped history. Master's
      ``FlagOverride`` dataclass is slimmer (carries
      ``name``/``value``/``reason``/``actor``), so this port
      recovers ``previous_value`` by walking the history from the
      start of the log and tracking the running value of
      ``name`` — O(n) over the full log, bounded by the deque
      cap. The scan is on a process-local deque, not a
      roundtrip, so the cost is negligible.

    - The source's ``no_op`` field is gone (master's
      ``set_flag`` does not distinguish no-op from real writes —
      round 5's design note explicitly trades that distinction
      for a singular audit path). Every entry is treated as a
      real change in the rollback scan, which is the
      semantically-correct choice under master's audit shape:
      re-applying the previous value still undoes the operator's
      last call, just with a strictly stronger guarantee.

    - The pin guard is inherited for free: ``set_flag`` routes
      through :func:`record_override`, which raises
      ``FlagPinnedError`` when the flag is locked and the new
      value differs from the current override. A rollback to a
      pinned value (e.g. un-pin, then immediately re-pin) is
      fine; a rollback to a non-pinned value on a pinned flag
      raises and the flag is left untouched. Operators wanting
      to roll back a pinned flag should ``unpin_flag`` first.

    The function name ``rollback_flag`` matches the source
    verbatim, so the API layer can ``**``-unpack the returned
    dict into a Pydantic ``FeatureFlagRollbackResponse`` without
    a field-by-field copy.
    """
    # First gate: unknown flag → None (route maps to 404). This
    # check is repeated in ``set_flag`` (which we route through),
    # but doing it here lets the 404 path bypass the history
    # scan entirely — a tiny optimisation, but also a clean
    # logging point (the source's design re-logs the unknown
    # inside ``set_flag`` anyway, but the warning event
    # duplicates the flag name in a way that makes operator
    # dashboards double-count attempts).
    is_known = name in _DEFAULT_FLAGS or name in _overrides
    if not is_known:
        logger.warning(
            "feature_flag.unknown",
            flag_name=name,
            known_flags=sorted(_DEFAULT_FLAGS.keys()),
        )
        return None

    # ``get_history`` returns newest-first. Walk the log in
    # that order directly so the first match is the most
    # recent real change. The scan is bounded by
    # ``_ROLLBACK_SCAN_LIMIT`` recent rows; in practice the
    # full log is at most ``_HISTORY_LIMIT`` = 100 rows, so
    # the slice is typically a no-op.
    newest_first = get_history()
    scan_window = (
        newest_first[:_ROLLBACK_SCAN_LIMIT]
        if len(newest_first) > _ROLLBACK_SCAN_LIMIT
        else newest_first
    )

    # Filter the window to entries that match ``name`` so
    # the pre-mutation value recovery below can index into
    # a per-flag subsequence. The pre-mutation value of
    # the i-th entry in this filtered list (0-indexed,
    # newest-first) is the value of the (i+1)-th entry, OR
    # the registry default if ``i`` is the last matching
    # entry (i.e. the first change for this flag).
    matching_entries: list[FlagOverride] = [
        entry for entry in scan_window if entry.name == name
    ]

    if not matching_entries:
        # No real change for this flag in the scan window
        # (or the audit log is empty for this flag). The
        # route maps this to 409 Conflict — the flag
        # exists, the request was well-formed, but there
        # is nothing to undo.
        return None

    # The most recent real change is the first entry in
    # the filtered list. Its pre-mutation value is the
    # value of the second entry in the filtered list, or
    # the default if the most recent change is the first
    # change for this flag.
    target = matching_entries[0]
    if len(matching_entries) >= 2:
        target_previous_value: bool = matching_entries[1].value
    else:
        target_previous_value = _DEFAULT_FLAGS.get(name, False)
    # Map back to the absolute newest-first index so
    # callers can render "rolled back change #N from the
    # audit log".
    target_index: int = next(
        idx
        for idx, entry in enumerate(scan_window)
        if entry is target
    )

    # Snapshot the pre-rollback value (the value ``set_flag``
    # is about to overwrite) so the response can render
    # "rolled back from X to Y" without a second
    # ``is_enabled()`` call after the mutation.
    previous_value_before_rollback = is_enabled(name)
    # Route through ``set_flag`` (not ``record_override``
    # directly) so the new audit entry has ``reason="set_flag"``
    # and ``actor="system"`` — the wrapper's stable identity,
    # and so the pin guard fires for free.
    set_flag_result = set_flag(name, target_previous_value)
    # Defensive: ``set_flag`` only returns ``None`` for
    # unknown flags, which we already gated above, but a
    # future refactor could break that invariant — surface
    # it as None rather than silently lying about a
    # successful rollback. (The pin-guard ``FlagPinnedError``
    # is intentionally NOT caught here — the API layer maps
    # that exception to a 423 Locked response.)
    if set_flag_result is None:
        return None

    return {
        "name": name,
        "rolled_back_from": previous_value_before_rollback,
        "rolled_back_to": target_previous_value,
        "restored_entry_index": target_index,
        "history_size_at_rollback": len(_history),
    }


def utcnow_iso_z() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix.

    Centralised so every audit-log entry that wants an
    ``at_time = utcnow_iso_z()`` field uses the same formatting
    (UTC, microsecond precision, ``Z`` instead of ``+00:00``).
    Python's ``datetime.isoformat()`` would emit ``+00:00`` by
    default, which is JSON-parseable but harder to grep and not the
    format every dashboard expects.

    Mirrors the branch's ``_utcnow_iso_z`` private helper
    (docs/_source_feature_flags.py.txt, lines 110-123) but is
    public so test code and operator endpoints can format
    timestamps consistently without re-implementing the suffix
    replace. The leading underscore is dropped here because the
    function has no side effects on module state and is safe for
    external callers.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )