"""Canonical mod-request status set and a pure validator helper.

Extracted from the discord-ops-hardening branch's ``storage/queries.py``
(``_VALID_STATUSES`` + ``is_valid_mod_status``, lines ~20-22 and 335-344
of ``docs/_source_queries.py.txt``). Hoisted into its own module so
non-query callers (tests, future Discord/cron admin commands) can validate
status strings without re-declaring the allowed set, and so the canonical
list has one home to drift-check against (mirrors the pattern in
``orchestrator/feature_flags.py``).

The Pydantic ``Literal["pending", "running", ...]`` declared on
``app.api.schemas.ModStatusResponse.status`` remains the *primary*
validation gate for HTTP traffic. This module is the second-line helper
for internal callers that don't go through the API layer, plus the test
suite's source of truth when asserting on the legal status set.

Pure functions only — no DB calls, no logging, no module state. Safe to
import from anywhere (including test fixtures and cron-style scripts).
"""
from __future__ import annotations

from typing import Final

# Canonical mod-request statuses. Kept as a frozenset (immutable) so any
# accidental mutation raises rather than silently changing the contract
# for every caller. The set mirrors the runtime state machine
# (pending -> running -> done/failed/cancelled) and the Pydantic Literal
# on app.api.schemas.ModStatusResponse.status (schemas.py L40). If you
# extend this set, update both this file AND the schemas.py Literal in
# the same commit so the two declarations cannot drift apart.
VALID_MOD_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "running", "done", "failed", "cancelled"}
)


def is_valid_mod_status(value: str) -> bool:
    """Return True iff ``value`` is one of the canonical mod-request statuses.

    Cheap, pure, no side effects. Use this from tests and internal
    callers (cron jobs, admin scripts, future Discord commands) when you
    need to validate a status string without going through the HTTP
    layer's Pydantic Literal.

    Args:
        value: The status string to validate. Case-sensitive — the
            canonical set is lowercase by convention, and anything else
            is rejected (the Pydantic Literal in the API layer behaves
            the same way).

    Returns:
        bool: True if ``value`` is in :data:`VALID_MOD_STATUSES`,
        False otherwise. Empty string and arbitrary objects that aren't
        strings at all return False (the function type-annotates the
        argument as ``str`` but defensively returns False for non-strings
        rather than raising — easier to call from untrusted sources
        without a try/except).
    """
    if not isinstance(value, str):
        return False
    return value in VALID_MOD_STATUSES
