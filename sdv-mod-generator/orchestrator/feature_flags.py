"""Feature flags: in-memory rollout controls with override history.

Minimal cleanroom port of the discord-ops-hardening branch's flag helper
(see docs/P3_P5_MERGE_PLAN.md — branch file at dfb3dd7 is 567 lines; this
file extracts the four self-contained helpers needed by gates and routes:
``is_enabled``, ``record_override``, ``list_pins``, ``get_history``).

State is process-local (a module-level dict + deque). Persistence,
Redis-backed rollout percentages, and admin endpoints are intentionally
out of scope — they require the rest of the branch's rollout stack and
land in a later PR.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


# Overrides win over defaults. The deque keeps the last N events for
# ``get_history``; ``_overrides`` is the current pin set.
_overrides: dict[str, bool] = {}
_history: deque[FlagOverride] = deque(maxlen=_HISTORY_LIMIT)


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
    """
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