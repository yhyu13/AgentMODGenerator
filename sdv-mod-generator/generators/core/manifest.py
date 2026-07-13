"""Shared Content Patcher manifest.json builder.

Used by every feature pack's content_json generator to emit the
``manifest.json`` that Content Patcher requires for the mod to load.
Without this file, Content Patcher silently rejects the mod — the zip
contains valid ``content.json`` + assets, but the mod never appears
in-game.

History (2026-07-12): the v169/v172 cron ports of the 2-generator
Tier-1 packs (weapon_definition, tool_definition, hat_collection, etc.)
inherited the discord-ops-hardening branch's source structure, which
has only a ``DefinitionGenerator`` + ``ContentJsonGenerator``. The
source bundles on that branch ship NO ``ManifestGenerator`` — every
mod is expected to either have its own manifest pack or run alongside
a shared one. This means the v169/v172 cron's products lacked
``manifest.json``, and the generated zips would fail to load in SDV.

Fix: this module provides a single shared helper every pack's
ContentJsonGenerator calls to emit a valid manifest alongside the
content.json it already produces. Larger diff per pack (one extra
``out.add_file`` call), but matches the existing weather_event /
achievements pattern (which have a separate ManifestGenerator that
calls the same shape).
"""
from __future__ import annotations

import re
from typing import Final


#: Content Patcher format version pinned across all packs on master.
#: Matches the value emitted by every existing pack's manifest +
#: content.json (verified against weather_event, achievements,
#: shop_channel, event_mod, texture, npc_schedule, farm_expansion,
#: custom_crafting, weapon_definition, tool_definition).
DEFAULT_FORMAT_VERSION: Final[str] = "1.29.0"

#: Content Patcher dependency target. Every pack depends on this
#: with the same MinimumVersion — see ``standards doc §3`` (the
#: `ContentPackFor` + `Dependencies` block).
CONTENT_PATCHER_UNIQUE_ID: Final[str] = "Pathoschild.ContentPatcher"
CONTENT_PATCHER_MIN_VERSION: Final[str] = "2.4.0"

#: Canonical author tag for LLM-generated mods.
DEFAULT_AUTHOR: Final[str] = "AI Generator"

#: Canonical version for a fresh mod. Bump only on schema breaks.
DEFAULT_VERSION: Final[str] = "1.0.0"

#: Slugification alphabet. Anything outside [a-z0-9_-] becomes "_".
_SLUG_OK = re.compile(r"[^a-z0-9_-]+")


def slugify_unique_id(
    text: str,
    prefix: str = "ai_generator.",
    default: str = "stardew_mod",
) -> str:
    """Convert arbitrary text to a Content Patcher UniqueID-friendly slug.

    Lowercases, replaces every non-[a-z0-9_-] run with a single
    underscore, strips leading/trailing underscores, and prefixes
    with the canonical author tag. The result is always a valid CP
    UniqueID (Content Patcher rejects spaces and most punctuation).

    Parameters
    ----------
    text:
        Source text — typically the user's prompt or a derived slug.
    prefix:
        Author prefix. Defaults to ``"ai_generator."`` per the
        project's standards doc §3.
    default:
        Fallback slug when ``text`` produces an empty string after
        sanitization (all whitespace, all punctuation, etc.).

    Examples
    --------
    >>> slugify_unique_id("add a custom sword weapon")
    'ai_generator.add_a_custom_sword_weapon'
    >>> slugify_unique_id("!!!")
    'ai_generator.stardew_mod'
    >>> slugify_unique_id("weapon-definition-2", prefix="")
    'weapon-definition-2'
    """
    base = _SLUG_OK.sub("_", text.lower()).strip("_")
    if not base:
        base = default
    return f"{prefix}{base}"


def build_manifest_dict(
    unique_id: str,
    name: str,
    description: str,
    *,
    author: str = DEFAULT_AUTHOR,
    version: str = DEFAULT_VERSION,
    format_version: str = DEFAULT_FORMAT_VERSION,
    content_patcher_min_version: str = CONTENT_PATCHER_MIN_VERSION,
) -> dict:
    """Build a Content Patcher-compliant manifest.json dict.

    The output matches the weather_event ManifestGenerator's shape
    (verified against the standards doc §3 examples). Always
    slugifies the UniqueID defensively — the LLM sometimes includes
    spaces or special characters despite prompt instructions, and
    Content Patcher rejects those silently.

    Parameters
    ----------
    unique_id:
        Raw UniqueID from the LLM (or a derived slug). Will be
        slugified defensively before insertion.
    name:
        Human-readable mod name. 3-7 words, Title Case per standards.
    description:
        1-2 sentence description specific to the mod's content.
        Generic descriptions ("adds stuff") fail T2 review.
    author:
        Defaults to ``"AI Generator"`` (canonical LLM-generated author).
    version:
        Defaults to ``"1.0.0"`` for new mods.
    format_version:
        Defaults to ``"1.29.0"`` (current Content Patcher format).
    content_patcher_min_version:
        Defaults to ``"2.4.0"`` (minimum Content Patcher version
        that supports all the features used by these packs).

    Returns
    -------
    dict[str, object]
        The manifest.json payload, ready to be passed to
        ``out.add_file("manifest.json", ...)``.

    Notes
    -----
    The output dict's keys are CP-canonical (Capitalized): Format,
    UniqueID, Name, Description, Author, Version, ContentPackFor,
    Dependencies. Do NOT lowercase them — Content Patcher is
    case-sensitive on these.
    """
    safe_unique_id = slugify_unique_id(unique_id)
    return {
        "Format": format_version,
        "UniqueID": safe_unique_id,
        "Name": name,
        "Description": description,
        "Author": author,
        "Version": version,
        "ContentPackFor": {
            "UniqueID": CONTENT_PATCHER_UNIQUE_ID,
            "MinimumVersion": content_patcher_min_version,
        },
        "Dependencies": [
            {
                "UniqueID": CONTENT_PATCHER_UNIQUE_ID,
                "MinimumVersion": content_patcher_min_version,
            },
        ],
    }


def fallback_name_from_prompt(prompt: str, default: str = "Stardew Mod") -> str:
    """Derive a human-readable Name from a prompt when the LLM doesn't.

    Title-cases the first 3-7 significant words from the prompt.
    Used by ContentJsonGenerator's manifest emission as the LLM-fallback
    path (the LLM may have been asked for content.json, not manifest).
    """
    # Strip punctuation, lowercase, take the first 5 words, title-case.
    cleaned = re.sub(r"[^a-zA-Z0-9 ]+", " ", prompt).strip()
    words = [w for w in cleaned.split() if len(w) > 1][:5]
    if not words:
        return default
    return " ".join(w.capitalize() for w in words)


__all__ = [
    "DEFAULT_FORMAT_VERSION",
    "CONTENT_PATCHER_UNIQUE_ID",
    "CONTENT_PATCHER_MIN_VERSION",
    "DEFAULT_AUTHOR",
    "DEFAULT_VERSION",
    "slugify_unique_id",
    "build_manifest_dict",
    "fallback_name_from_prompt",
]