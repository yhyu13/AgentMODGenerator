"""SMAPI console log parser for the real-load gate.

Pure functions only, so failure detection can be unit tested without
launching the game. Used by ``tests/test_smapi_real_load.py``.
"""
from __future__ import annotations

#: Lines matching any of these (case-insensitively) are hard failures.
HARD_FAIL_PATTERNS = (
    "this mod failed",
    "could not be loaded",
    "error loading mod",
    "can't apply data patch",
    "mod failed to load",
    # Content Patcher reports a broken content.json (e.g. a CP 2.x EditMap
    # MapTiles entry whose ``Position`` is a string instead of an object)
    # as "Error preloading content pack '<name>'." — a hard failure the
    # older patterns above don't match.
    "error preloading content pack",
    # SMAPI emits "These mods could not be added to your game." + a
    # "- <Name> because ..." line for skipped mods, and the per-mod TRACE
    # line says "Failed: ...". The most common cause is a duplicate
    # UniqueID ("multiple copies of this mod installed") — the symptom
    # that surfaced when two generated weapon mods shared the hardcoded
    # UniqueID. Neither phrase matched the older patterns, so a
    # duplicate-id mod was silently green.
    "could not be added to your game",
    "multiple copies of this mod",
)

#: Markers that turn a Content Patcher "Ignored ..." skip into a failure.
IGNORED_FAIL_MARKERS = (
    "invalid",
    "can't be used",
    "could not be found as a token",
)


def find_smapi_failures(log_text: str) -> list[str]:
    """Return the log lines that indicate a mod failed to load.

    Two rules, both case-insensitive:

    * a hard failure phrase like ``this mod failed`` or ``Can't apply
      data patch``;
    * a Content Patcher skip (``Ignored ...``) that also contains an
      invalid-patch marker (``invalid``, ``can't be used``, ``could not
      be found as a token``).

    Benign lines (update checks, runtime migration notices, etc.) never
    match, so the gate only fires on genuinely broken patches.
    """
    failures: list[str] = []
    for line in log_text.splitlines():
        lowered = line.lower()
        if any(pattern in lowered for pattern in HARD_FAIL_PATTERNS):
            failures.append(line)
        elif "ignored" in lowered and any(
            marker in lowered for marker in IGNORED_FAIL_MARKERS
        ):
            failures.append(line)
    return failures
