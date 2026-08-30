"""Custom weapon-definition feature pack for Stardew Valley.

Generates a Content Patcher mod that adds 1-4 custom weapons to the game
by editing ``Data/Weapons`` (one additive ``custom_weapon_<token>`` row
per weapon) and registering a small ``Strings/UI`` shim with the
per-weapon display name + description.

Vanilla SDV's ``Data/Weapons`` table is a flat dictionary keyed by
weapon id; each row's body carries ``Name`` (lang key), ``Description``
(lang key), ``MinDamage``/``MaxDamage`` (integer damage envelope, with
``MaxDamage >= MinDamage``), ``CritChance`` (float 0.0-1.0),
``CritMultiplier`` (float 1.0-5.0), ``Speed`` (integer 0-15), ``Type``
(one of ``Sword``, ``Club``, ``Dagger``, ``Slingshot``), ``Texture``
(relative asset path under ``Weapons/``), and ``DisplayName`` (the lang
key, typically ``"Weapon.<ItemId>.Name"``).

Distinct from neighbouring packs:

- ``boot_collection`` (v91, ``Data/Boots``) — boots are wearables with
  ``Defense``/``Immunity``/``Price``/``Color`` envelopes; weapons have
  ``MinDamage``/``MaxDamage``/``CritChance``/``Speed`` envelopes.
- ``hat_collection`` (v90, ``Data/Hats``) — hats are headwear with no
  stat envelope; weapons have a damage envelope.
- ``custom_powers`` (v92, ``Data/Powers``) — powers are passive
  abilities shown in the Skills tab; weapons are equippable items with
  damage stats.

The pack is composed of two cooperating generators that mirror the
shape of ``npc_portrait`` (v82), ``npc_disposition`` (v80),
``furniture_definition`` (v81), ``horse_breed`` (v87),
``witch_swamp`` (v88), ``movie_theater`` (v89), ``hat_collection``
(v90), ``boot_collection`` (v91), and ``custom_powers`` (v92) — the
smaller 2-gen packs:

1. :class:`WeaponDefinitionDefinitionGenerator` — defines 1-4 custom
   weapons. Each weapon row carries an ``ItemId`` (snake_case, max 32
   chars, must be prefixed with ``"custom_weapon_"``), ``Name`` (short
   printable weapon name, max 32 chars), ``Description`` (one-line
   flavour text, max 128 chars), ``MinDamage`` (int 0-200),
   ``MaxDamage`` (int 0-200, must be ``>= MinDamage``), ``CritChance``
   (float 0.0-1.0), ``CritMultiplier`` (float 1.0-5.0), ``Speed``
   (int 0-15), ``Type`` (one of ``Sword``/``Club``/``Dagger``/
   ``Slingshot``), ``Texture`` (asset path under ``Weapons/``), and
   ``DisplayName`` (the lang key for the per-weapon display name; we
   mirror ``"Weapon.<ItemId>.Name"``). LLM-call + 2-weapon hardcoded
   fallback (Wood Sword, Iron Dagger) so the pipeline still produces a
   loadable mod when no LLM is configured. (Lands in v169.)
2. :class:`WeaponDefinitionContentJsonGenerator` — assembles the final
   ``content.json`` that edits ``Data/Weapons`` (one additive
   ``custom_weapon_<token>`` row per weapon) and registers a small
   ``Strings/UI`` shim with the per-weapon display name + description.
   Reads the manifest mod-id from ``prior_outputs`` defensively with a
   hardcoded fallback. (Lands in v170.)

This generator set is intentionally LLM-optional: every generator has a
deterministic fallback path so the pipeline still produces a usable
mod when no LLM is configured. The fallback emits 2 custom weapons
(``custom_weapon_wood_sword``, ``custom_weapon_iron_dagger``) that
demonstrate the per-row additive pattern without colliding with any
vanilla SDV weapon keys. The ``"custom_weapon_"`` prefix keeps the
additive ``Entries`` block safe.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

import structlog

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.core.manifest import (
    build_manifest_dict,
    fallback_name_from_prompt,
    slugify_unique_id,
)
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger(__name__)


# Content Patcher format version. Matches the convention used by all
# existing master packs (achievements, shop_channel, event_mod,
# texture, npc_schedule, farm_expansion): hardcoded as a per-pack
# constant. v170's WeaponDefinitionContentJsonGenerator will reference
# this constant for its ``Format`` field.
_FORMAT_VERSION: str = "1.29.0"


# Vanilla SDV weapon ``Name`` lang-key template. The vanilla
# ``Data/Weapons`` rows use the convention ``"Weapon.<id>.Name"`` for
# the human-readable weapon name and ``"Weapon.<id>.Description"``
# for the description text. We mirror that convention with the modded
# ``custom_weapon_``-prefixed token.
_NAME_KEY_TEMPLATE: str = "Weapon.{item_id}.Name"
_DESCRIPTION_KEY_TEMPLATE: str = "Weapon.{item_id}.Description"


# Per-pack weapon count envelope. 1-4 weapons is the canonical sweet
# spot for an "add custom weapons to my mod" prompt; cap at 4 to keep
# the per-row body of the content.json reasonable and to mirror the
# v92 ``custom_powers``, v91 ``boot_collection``, and v90
# ``hat_collection`` envelopes.
_MIN_WEAPONS: int = 1
_MAX_WEAPONS: int = 4
_DEFAULT_WEAPONS: int = 2


# Per-weapon token length cap. Vanilla SDV weapon ids are short, but
# Content Patcher can handle up to 64 chars without issue; we cap at
# 32 to mirror the v92 ``custom_powers``, v91 ``boot_collection``,
# and v90 ``hat_collection`` pack conventions.
_WEAPON_TOKEN_MAX_LEN: int = 32


# Display name length cap. Vanilla SDV's weapon-slot UI uses ~32 chars
# per weapon display name. Mirrors the v92 ``custom_powers`` pack
# convention.
_DISPLAY_NAME_MAX_LEN: int = 32


# Weapon description length cap. One-line flavour text shown beneath
# the weapon name in the weapon-slot UI; cap at 128 chars to allow a
# comfortable two-sentence description. Mirrors the v92
# ``custom_powers`` pack convention.
_DESCRIPTION_MAX_LEN: int = 128


# Asset-path length cap. Vanilla SDV asset paths are short; cap at
# 96 chars to allow a reasonable ``Weapons/<weapon_token>``-style
# layout. Mirrors the v92 ``custom_powers`` pack convention.
_ASSET_PATH_MAX_LEN: int = 96


# Vanilla SDV ``Data/Weapons`` damage envelope. The vanilla
# ``MinDamage``/``MaxDamage`` fields are integers ``0-200`` (a typical
# vanilla Sword is 2-3/5-9; the highest-damage Galaxy weapons cap at
# 30-65; the Slingshot can spike higher with ``Explosive Ammo``).
# Out-of-range values would crash the damage-calc UI. Mirrors the
# v82 ``npc_portrait`` convention of pinning integer envelopes
# against vanilla-SDV ranges.
_MIN_DAMAGE: int = 0
_MAX_DAMAGE: int = 200


# Vanilla SDV ``Data/Weapons`` crit envelope. ``CritChance`` is a
# float ``0.0-1.0`` (vanilla Swords ~0.02, Daggers ~0.05, Slingshots
# ~0.02) and ``CritMultiplier`` is a float ``1.0-5.0`` (vanilla
# defaults to 3.0 but allows higher/lower). Values outside these
# envelopes would be silently dropped by the combat-engine damage
# formula.
_MIN_CRIT_CHANCE: float = 0.0
_MAX_CRIT_CHANCE: float = 1.0
_DEFAULT_CRIT_CHANCE: float = 0.02
_MIN_CRIT_MULTIPLIER: float = 1.0
_MAX_CRIT_MULTIPLIER: float = 5.0
_DEFAULT_CRIT_MULTIPLIER: float = 3.0


# Vanilla SDV ``Data/Weapons`` speed envelope. The ``Speed`` field is
# an integer ``0-15`` (lower = faster swing; vanilla Sword=0,
# Club=8, Dagger=0, Slingshot=7). Out-of-range values would crash
# the swing-cooldown UI.
_MIN_SPEED: int = 0
_MAX_SPEED: int = 15
_DEFAULT_SPEED: int = 0


# Vanilla SDV ``Data/Weapons`` type enum. The ``Type`` field is a
# string drawn from this set. Content Patcher silently drops an
# unknown ``Type`` value, falling back to ``Sword`` behaviour; we
# snap LLM-supplied types to this enum defensively.
_WEAPON_TYPES: frozenset[str] = frozenset(
    {"Sword", "Club", "Dagger", "Slingshot"}
)
_DEFAULT_WEAPON_TYPE: str = "Sword"


# Vanilla SDV uses Title-Case weapon keys ("Rusty Sword",
# "Iron Dagger"). The ``"custom_weapon_"`` prefix keeps the additive
# ``Entries`` block from colliding with any vanilla SDV weapon ids;
# we mirror the convention used by ``boot_collection`` (v91),
# ``hat_collection`` (v90), ``custom_powers`` (v92),
# ``furniture_definition`` (v81), and ``object_definition`` (v78).
_WEAPON_TOKEN_PREFIX: str = "custom_weapon"


# Default mod id fallback (matches the v92 ``custom_powers`` pack
# convention of "lower-case mod id with a dot").
_DEFAULT_MOD_ID: str = "custom.weapondefinition"


# Default texture asset-path template. Used when the LLM does not
# supply a texture path so the deterministic fallback still produces
# a loadable Content Patcher manifest.
_DEFAULT_TEXTURE_TEMPLATE: str = "Weapons/{weapon_token}"


# SDV 1.6 ``Data/Weapons`` raw field layout. Content Patcher edits
# ``Data/Weapons`` as a ``Dictionary<string, string>`` whose values
# are pipe-delimited rows in the pre-1.6 15-field format (see CP's
# ``Migration_2_0.ForWeapons``), then merges each row back into the
# structured ``WeaponData`` model on load. Field order (index):
# 0 Name, 1 Description, 2 MinDamage, 3 MaxDamage, 4 Knockback,
# 5 Speed, 6 Precision, 7 Defense, 8 Type, 9 MineBaseLevel,
# 10 MineMinLevel, 11 AreaOfEffect, 12 CritChance, 13 CritMultiplier,
# 14 DisplayName.
_WEAPON_RAW_FIELD_COUNT: int = 15


# SDV 1.6 ``WeaponData.Type`` is an integer enum: 0 (stabbing sword),
# 1 (dagger), 2 (club or hammer), 3 (slashing sword), 4 (slingshot).
# Maps the per-pack ``Type`` string enum onto the numeric ids used by
# the raw ``Data/Weapons`` rows.
_WEAPON_TYPE_IDS: dict[str, int] = {
    "Sword": 0,
    "Dagger": 1,
    "Club": 2,
    "Slingshot": 4,
}


# ---------------------------------------------------------------------
# Sanitizers
# ---------------------------------------------------------------------


def _sanitize_weapon_token(raw: object) -> str:
    """Return a safe snake_case weapon token (max 32 chars).

    Mirrors ``_sanitize_power_token`` from the v92 ``custom_powers``
    pack and ``_sanitize_boot_token`` from the v91 ``boot_collection``
    pack: stripped to alphanumerics + underscore, lowercased, prefixed
    with ``"custom_weapon_"`` if it would otherwise start with a digit
    or be missing the prefix, and capped at 32 chars. Always returns
    a non-empty string.
    """
    if raw is None:
        return f"{_WEAPON_TOKEN_PREFIX}_default"
    text = str(raw)
    cleaned = "".join(
        c for c in text.lower() if c.isalnum() or c == "_"
    )
    if not cleaned:
        return f"{_WEAPON_TOKEN_PREFIX}_default"
    if cleaned[0].isdigit():
        cleaned = f"{_WEAPON_TOKEN_PREFIX}_" + cleaned
    if not cleaned.startswith(f"{_WEAPON_TOKEN_PREFIX}_"):
        cleaned = f"{_WEAPON_TOKEN_PREFIX}_" + cleaned
    return cleaned[:_WEAPON_TOKEN_MAX_LEN] or (
        f"{_WEAPON_TOKEN_PREFIX}_default"
    )


def _sanitize_damage(
    raw: object,
    *,
    min_value: int = _MIN_DAMAGE,
    max_value: int = _MAX_DAMAGE,
) -> int:
    """Coerce a damage value to an int clamped to ``[min_value, max_value]``.

    Mirrors ``_sanitize_int_clamped`` from the v91 ``boot_collection``
    pack but specialised for the vanilla-SDV ``Data/Weapons`` damage
    envelope.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return min_value
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _sanitize_crit_chance(raw: object) -> float:
    """Coerce a crit-chance value to a float in ``[0.0, 1.0]``.

    Mirrors ``_sanitize_damage`` but specialised for the vanilla-SDV
    ``Data/Weapons`` ``CritChance`` envelope (float ``0.0-1.0``).
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_CRIT_CHANCE
    if value < _MIN_CRIT_CHANCE:
        return _MIN_CRIT_CHANCE
    if value > _MAX_CRIT_CHANCE:
        return _MAX_CRIT_CHANCE
    return value


def _sanitize_crit_multiplier(raw: object) -> float:
    """Coerce a crit-multiplier value to a float in ``[1.0, 5.0]``.

    Mirrors ``_sanitize_crit_chance`` but with the vanilla-SDV
    ``Data/Weapons`` ``CritMultiplier`` envelope (float ``1.0-5.0``).
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_CRIT_MULTIPLIER
    if value < _MIN_CRIT_MULTIPLIER:
        return _MIN_CRIT_MULTIPLIER
    if value > _MAX_CRIT_MULTIPLIER:
        return _MAX_CRIT_MULTIPLIER
    return value


def _sanitize_speed(raw: object) -> int:
    """Coerce a speed value to an int in ``[0, 15]``.

    Mirrors ``_sanitize_damage`` but specialised for the vanilla-SDV
    ``Data/Weapons`` ``Speed`` envelope (int ``0-15``).
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_SPEED
    if value < _MIN_SPEED:
        return _MIN_SPEED
    if value > _MAX_SPEED:
        return _MAX_SPEED
    return value


def _sanitize_weapon_type(raw: object) -> str:
    """Snap an LLM-supplied weapon type to the vanilla-SDV enum.

    The vanilla ``Data/Weapons`` ``Type`` field is one of ``Sword``,
    ``Club``, ``Dagger``, ``Slingshot``; Content Patcher silently
    drops an unknown value and falls back to ``Sword`` behaviour. We
    snap LLM-supplied values to the canonical enum with case-
    insensitive matching, falling back to ``_DEFAULT_WEAPON_TYPE``
    (``Sword``) when no canonical match is found.
    """
    if isinstance(raw, str):
        text = raw.strip().title()
        if text in _WEAPON_TYPES:
            return text
    return _DEFAULT_WEAPON_TYPE


def _sanitize_display_name(raw: object) -> str:
    """Coerce a string into a printable display name (max 32 chars).

    Mirrors ``_sanitize_display_name`` from the v92 ``custom_powers``
    and v91 ``boot_collection`` packs: filters control characters,
    collapses whitespace, caps at 32 chars with an ellipsis suffix
    if truncated. Falls back to ``""`` for non-string inputs so the
    downstream content.json generator can swap in a default.
    """
    if not isinstance(raw, str):
        return ""
    cleaned = "".join(
        ch for ch in raw
        if ch.isprintable() or ch in ("\n", "\t", " ")
    )
    text = " ".join(cleaned.split()).strip()
    if len(text) > _DISPLAY_NAME_MAX_LEN:
        text = text[: _DISPLAY_NAME_MAX_LEN - 1].rstrip() + "\u2026"
    return text


def _sanitize_description(raw: object) -> str:
    """Coerce a string into a printable description (max 128 chars).

    Mirrors ``_sanitize_description`` from the v92 ``custom_powers``
    and v91 ``boot_collection`` packs: filters control characters,
    collapses whitespace, caps at 128 chars with an ellipsis suffix
    if truncated. Falls back to ``""`` for non-string inputs.
    """
    if not isinstance(raw, str):
        return ""
    cleaned = "".join(
        ch for ch in raw
        if ch.isprintable() or ch in ("\n", "\t", " ")
    )
    text = " ".join(cleaned.split()).strip()
    if len(text) > _DESCRIPTION_MAX_LEN:
        text = text[: _DESCRIPTION_MAX_LEN - 1].rstrip() + "\u2026"
    return text


def _sanitize_texture(
    raw: object, default_template: str, weapon_token: str
) -> str:
    """Coerce an asset path string into a safe relative path.

    Falls back to the per-pack default template if the input is
    missing or invalid. Mirrors ``_sanitize_texture`` from the v92
    ``custom_powers`` and v91 ``boot_collection`` packs.

    Hardening: any path-traversal segment (``..`` or absolute prefix)
    collapses to the default template so a malicious LLM response
    cannot smuggle ``../../../etc/passwd`` into the downstream
    ``content.json`` (which Content Patcher would then write to disk
    inside the generated mod zip).
    """
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.startswith("/"):
            text = text.lstrip("/")
        # Path-traversal guard — reject any segment that is ``..``
        # or starts with ``..`` (handles both ``../foo`` and
        # ``foo/../bar`` forms by splitting on forward slashes and
        # back-slashes). Content Patcher asset paths use forward
        # slashes only; we also strip back-slashes to defend against
        # Windows-style traversal tokens.
        if ".." in text.replace("\\", "/").split("/"):
            return default_template.format(weapon_token=weapon_token)
        if text:
            return text[:_ASSET_PATH_MAX_LEN]
    return default_template.format(weapon_token=weapon_token)


def _sanitize_count(raw: object) -> int:
    """Coerce a value to an int clamped to ``[_MIN_WEAPONS, _MAX_WEAPONS]``.

    Falls back to ``_DEFAULT_WEAPONS`` for non-numeric inputs.
    Mirrors ``_sanitize_count`` from the v92 ``custom_powers`` and
    v91 ``boot_collection`` packs.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_WEAPONS
    if value < _MIN_WEAPONS:
        return _MIN_WEAPONS
    if value > _MAX_WEAPONS:
        return _MAX_WEAPONS
    return value


def _sanitize_pipe_field(raw: str) -> str:
    """Make a text field safe for inclusion in a pipe-delimited row.

    The ``Data/Weapons`` raw row splits fields on ``/``, so any
    ``/`` in a text value would shift every later field. Replace it
    with ``-`` (kept out of the per-weapon display string shim since
    that shim is resolved at runtime, not parsed).
    """
    return raw.replace("/", "-")


def _localized_text_token(translation_key: str) -> str:
    """Wrap a translation key in the game's ``[LocalizedText ...]`` token.

    Vanilla weapon rows store their display name and description as
    tokenizable strings like ``[LocalizedText Strings\\Weapons:X]``.
    We mirror that with the per-weapon ``Strings/UI`` keys so the
    game's ``TokenParser`` resolves the actual display text at
    runtime (this also keeps the raw row free of ``/`` characters).
    """
    return f"[LocalizedText Strings\\UI:{translation_key}]"


# ---------------------------------------------------------------------
# Pydantic models (LLM structured-output shape)
# ---------------------------------------------------------------------


class WeaponSchema(BaseModel):
    """Schema for a single weapon definition.

    Mirrors the per-row shape used by the v92 ``custom_powers`` pack's
    ``PowerSchema`` but adapted for weapon metadata. The 10 fields
    here cover the canonical SDV weapon row: token id, display name,
    description, damage envelope, crit envelope, speed, type, texture
    path, and display-name lang key.
    """

    ItemId: str = Field(
        ...,
        description=(
            "Snake_case weapon id (prefixed custom_weapon_, "
            "max 32 chars)"
        ),
    )
    Name: str = Field(..., description="Short printable weapon name")
    Description: str = Field(
        ..., description="One-line flavour text (<= 128 chars)"
    )
    MinDamage: int = Field(
        ..., description="Integer min damage (0-200)"
    )
    MaxDamage: int = Field(
        ..., description="Integer max damage (0-200, >= MinDamage)"
    )
    CritChance: float = Field(
        ..., description="Crit chance (float 0.0-1.0)"
    )
    CritMultiplier: float = Field(
        ..., description="Crit multiplier (float 1.0-5.0)"
    )
    Speed: int = Field(
        ..., description="Swing speed (int 0-15; lower = faster)"
    )
    Type: str = Field(
        ...,
        description=(
            "Weapon type — one of Sword / Club / Dagger / "
            "Slingshot"
        ),
    )
    Texture: str = Field(
        ..., description="Asset path under Weapons/"
    )
    DisplayName: str = Field(
        ...,
        description=(
            "Lang key for the per-weapon display name "
            "(typically 'Weapon.<ItemId>.Name')"
        ),
    )


class WeaponListSchema(BaseModel):
    """Wrapper for the LLM's structured-output response.

    The list is capped at ``_MAX_WEAPONS * 2`` to defend against
    runaway LLM responses (mirrors the v92 ``custom_powers`` and
    v91 ``boot_collection`` pack conventions).
    """

    weapons: list[WeaponSchema] = Field(
        ...,
        max_length=_MAX_WEAPONS * 2,
        description="Custom weapons to add",
    )


# ---------------------------------------------------------------------
# Fallback data
# ---------------------------------------------------------------------


def _fallback_weapon(
    *,
    item_id: str,
    name: str,
    description: str,
    min_damage: int,
    max_damage: int,
    crit_chance: float,
    crit_multiplier: float,
    speed: int,
    weapon_type: str,
) -> dict[str, object]:
    """Construct one curated weapon row for the no-LLM path.

    Mirrors ``_fallback_boot`` from the v91 ``boot_collection`` pack
    but with the per-weapon shape (10 fields vs 9 for boot_collection
    — adds ``Type`` and drops ``Immunity``/``Color``).
    """
    texture_path = _DEFAULT_TEXTURE_TEMPLATE.format(
        weapon_token=item_id
    )
    return {
        "ItemId": item_id,
        "Name": name,
        "Description": description,
        "MinDamage": min_damage,
        "MaxDamage": max_damage,
        "CritChance": crit_chance,
        "CritMultiplier": crit_multiplier,
        "Speed": speed,
        "Type": weapon_type,
        "Texture": texture_path,
        "DisplayName": _NAME_KEY_TEMPLATE.format(
            item_id=item_id
        ),
    }


def _fallback_weapon_list() -> list[dict[str, object]]:
    """Return the deterministic 2-weapon fallback list.

    The 2 curated weapons (Wood Sword, Iron Dagger) cover two vanilla
    SDV weapon archetypes (Sword — balanced mid-tier, Dagger — fast
    low-damage) so the fallback demonstrates the per-row additive
    pattern with realistic damage stats. 2 weapons is intentionally
    small (matches ``boot_collection``'s fallback size) because the
    weapon-slot UI is a tighter-scope surface with a more meaningful
    per-row design space — a 2-weapon fallback is enough to
    demonstrate the pattern without padding with low-value entries.

    Mirrors ``_fallback_boot_list`` from the v91 ``boot_collection``
    pack and ``_fallback_power_list`` from the v92 ``custom_powers``
    pack.
    """
    return [
        _fallback_weapon(
            item_id=f"{_WEAPON_TOKEN_PREFIX}_wood_sword",
            name="Wood Sword",
            description=(
                "A sturdy wooden practice sword — "
                "perfect for first-time adventurers."
            ),
            min_damage=2,
            max_damage=5,
            crit_chance=0.02,
            crit_multiplier=2.0,
            speed=0,
            weapon_type="Sword",
        ),
        _fallback_weapon(
            item_id=f"{_WEAPON_TOKEN_PREFIX}_iron_dagger",
            name="Iron Dagger",
            description=(
                "A swift iron dagger with a razor-sharp "
                "edge. +5% crit chance over the wood sword."
            ),
            min_damage=3,
            max_damage=7,
            crit_chance=0.05,
            crit_multiplier=3.0,
            speed=0,
            weapon_type="Dagger",
        ),
    ]


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------


def _sanitize_weapon_row(row: dict[str, object]) -> dict[str, object]:
    """Apply per-field sanitizers to one LLM-emitted row.

    Mirrors the in-place sanitization done by the v92 ``custom_powers``
    pack's ``_sanitize_power_row``, factored into a helper for
    readability given the per-weapon shape (10 fields vs 6 for
    custom_powers).
    """
    raw_token = row.get("ItemId", "")
    token = _sanitize_weapon_token(raw_token)
    name = _sanitize_display_name(row.get("Name", ""))
    if not name:
        bare = token[len(_WEAPON_TOKEN_PREFIX) + 1:]
        name = bare.replace("_", " ").title() or token
    description = _sanitize_description(
        row.get("Description", "")
    )
    texture = _sanitize_texture(
        row.get("Texture", ""),
        _DEFAULT_TEXTURE_TEMPLATE,
        token,
    )
    min_damage = _sanitize_damage(row.get("MinDamage", 0))
    max_damage = _sanitize_damage(
        row.get("MaxDamage", min_damage)
    )
    if max_damage < min_damage:
        max_damage = min_damage
    crit_chance = _sanitize_crit_chance(
        row.get("CritChance", _DEFAULT_CRIT_CHANCE)
    )
    crit_multiplier = _sanitize_crit_multiplier(
        row.get("CritMultiplier", _DEFAULT_CRIT_MULTIPLIER)
    )
    speed = _sanitize_speed(row.get("Speed", _DEFAULT_SPEED))
    weapon_type = _sanitize_weapon_type(
        row.get("Type", _DEFAULT_WEAPON_TYPE)
    )
    display_name = row.get("DisplayName", "")
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
    ):
        display_name = _NAME_KEY_TEMPLATE.format(
            item_id=token
        )
    return {
        "ItemId": token,
        "Name": name,
        "Description": description,
        "MinDamage": min_damage,
        "MaxDamage": max_damage,
        "CritChance": crit_chance,
        "CritMultiplier": crit_multiplier,
        "Speed": speed,
        "Type": weapon_type,
        "Texture": texture,
        "DisplayName": display_name,
    }


# ---------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------


class WeaponDefinitionDefinitionGenerator(BaseGenerator):
    """Define custom weapons for a Stardew Valley mod."""

    name = "weapon_definition_definition_generator"
    phase = "weapon_definition"
    game = "stardew_valley"

    async def generate(
        self, inp: GeneratorInput
    ) -> GeneratorOutput:
        """Build the 1-4 weapon definition list.

        On LLM failure, the deterministic fallback path emits 2
        curated weapons (Wood Sword, Iron Dagger) so the pipeline
        still produces a loadable Content Patcher manifest. Mirrors
        the v92 ``custom_powers`` pack's
        ``CustomPowerDefinitionGenerator.generate`` and the v91
        ``boot_collection`` pack's
        ``BootCollectionDefinitionGenerator.generate``.
        """
        out = GeneratorOutput()
        prompt = (
            f"Create custom Data/Weapons rows for a "
            f"Stardew Valley mod based on: "
            f"\"{inp['prompt']}\"\n\n"
            "Generate 1-4 unique weapon entries. For each "
            "weapon provide:\n"
            "- ItemId: snake_case id, max 32 chars, "
            "must be prefixed with \"custom_weapon_\"\n"
            "- Name: short printable weapon name, max 32 chars\n"
            "- Description: one-line flavour text, max "
            "128 chars\n"
            "- MinDamage: integer 0-200\n"
            "- MaxDamage: integer 0-200 (must be >= MinDamage)\n"
            "- CritChance: float 0.0-1.0\n"
            "- CritMultiplier: float 1.0-5.0\n"
            "- Speed: integer 0-15 (lower = faster swing)\n"
            "- Type: one of Sword / Club / Dagger / Slingshot\n"
            "- Texture: relative asset path under "
            "Weapons/<weapon_token>/\n"
            "- DisplayName: lang key for the per-weapon "
            "display name (typically 'Weapon.<ItemId>.Name')\n\n"
            "Respond with ONLY valid JSON matching the "
            "expected schema."
        )

        try:
            result = await generate_structured(
                prompt,
                WeaponListSchema,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            parsed = WeaponListSchema(**result)
            weapons: list[dict[str, object]] = []
            for w in parsed.weapons[:_MAX_WEAPONS]:
                token = _sanitize_weapon_token(w.ItemId)
                weapons.append(
                    {
                        "ItemId": token,
                        "Name": w.Name,
                        "Description": w.Description,
                        "MinDamage": w.MinDamage,
                        "MaxDamage": w.MaxDamage,
                        "CritChance": w.CritChance,
                        "CritMultiplier": w.CritMultiplier,
                        "Speed": w.Speed,
                        "Type": w.Type,
                        "Texture": w.Texture,
                        "DisplayName": w.DisplayName,
                    }
                )
            if not weapons:
                raise ValueError("no weapons produced")
        except (
            ValueError, RuntimeError, IOError, ValidationError
        ) as exc:
            logger.error(
                "weapon_definition_definition_generator.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            weapons = _fallback_weapon_list()

        # Sanitize each row defensively. A malicious or malformed
        # LLM response cannot bypass these clamps.
        sanitized: list[dict[str, object]] = []
        for row in weapons:
            sanitized.append(_sanitize_weapon_row(row))

        out.add_file(
            "assets/weapon_definition/weapons.json",
            {"weapons": sanitized},
        )
        out.metadata["weapon_count"] = len(sanitized)
        out.metadata["weapon_ids"] = [
            w["ItemId"] for w in sanitized
        ]
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        """Pin the per-row shape contract.

        Required keys, weapon list non-empty, each row has
        ``ItemId`` + ``Name`` + ``Texture`` + ``DisplayName`` +
        ``Type``, and ``ItemId`` starts with the
        ``"custom_weapon_"`` prefix.
        """
        errors: list[str] = []
        data = output.files.get(
            "assets/weapon_definition/weapons.json"
        )
        if not data:
            errors.append(
                "weapon_definition_definition_generator: "
                "assets/weapon_definition/weapons.json missing"
            )
            return errors
        if not isinstance(data, dict):
            errors.append(
                "weapon_definition_definition_generator: "
                "weapons.json must be a dict"
            )
            return errors
        weapons = data.get("weapons")
        if (
            not isinstance(weapons, list)
            or len(weapons) < _MIN_WEAPONS
        ):
            errors.append(
                "weapon_definition_definition_generator: "
                "weapons list missing or too short"
            )
            return errors
        seen_tokens: set[str] = set()
        for w in weapons:
            if not isinstance(w, dict):
                errors.append(
                    "weapon_definition_definition_generator: "
                    "each weapon item must be a dict"
                )
                continue
            for required in (
                "ItemId",
                "Name",
                "Description",
                "MinDamage",
                "MaxDamage",
                "CritChance",
                "CritMultiplier",
                "Speed",
                "Type",
                "Texture",
                "DisplayName",
            ):
                if required not in w:
                    errors.append(
                        "weapon_definition_definition_generator: "
                        f"missing {required!r}"
                    )
            token = w.get("ItemId")
            if isinstance(token, str):
                if token.lower() in seen_tokens:
                    errors.append(
                        "weapon_definition_definition_generator: "
                        f"duplicate ItemId {token!r}"
                    )
                seen_tokens.add(token.lower())
                if not token.startswith(f"{_WEAPON_TOKEN_PREFIX}_"):
                    errors.append(
                        "weapon_definition_definition_generator: "
                        f"ItemId {token!r} missing "
                        f"'{_WEAPON_TOKEN_PREFIX}_' prefix"
                    )
        return errors


class WeaponDefinitionContentJsonGenerator(BaseGenerator):
    """Assemble the final content.json for Content Patcher.

    Reads the manifest mod-id from ``prior_outputs`` defensively
    with a hardcoded fallback to ``_DEFAULT_MOD_ID``, and reads
    the per-weapon data from
    ``prior_outputs["weapon_definition_definition_generator"]``
    defensively with a hardcoded fallback to the 2-weapon
    deterministic fallback list. Emits one ``Data/Weapons``
    ``EditData`` change (per weapon) plus a small ``Strings/UI``
    shim with the per-weapon display name + description.

    Mirrors the v92 ``custom_powers`` pack's
    ``CustomPowerContentJsonGenerator`` and the v91
    ``boot_collection`` pack's
    ``BootCollectionContentJsonGenerator`` — same prior-outputs
    defensive-read pattern, same content.json shape.
    """

    name = "weapon_definition_content_json_generator"
    phase = "weapon_definition"
    game = "stardew_valley"

    async def generate(
        self, inp: GeneratorInput
    ) -> GeneratorOutput:
        """Build the content.json by emitting one
        Data/Weapons EditData change (per weapon) plus a
        small Strings/UI shim for the display names +
        descriptions.

        Reads the definition generator's prior output and
        the manifest mod-id defensively with a hardcoded
        fallback. Mirrors the v92 ``custom_powers`` pack's
        ``CustomPowerContentJsonGenerator``.
        """
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {}) or {}

        # Manifest mod id (lower-case per Content Patcher
        # convention). This phase has no dedicated manifest generator
        # (the router yields only the definition + content-json
        # generators), so prior_outputs never carries a
        # "manifest_generator" entry and the hardcoded fallback below
        # was the ONLY id ever used. That made every weapon mod emit
        # the same UniqueID, and SMAPI rejects the second one with
        # "multiple copies of this mod installed". Derive a
        # prompt-unique id instead so distinct prompts yield distinct
        # mods; keep the hardcoded default only for an empty prompt
        # (the deterministic fallback path used by tests).
        manifest_data = prior.get(
            "manifest_generator", GeneratorOutput()
        )
        mod_id = _DEFAULT_MOD_ID
        if isinstance(manifest_data, GeneratorOutput):
            manifest = manifest_data.files.get("manifest.json")
            if isinstance(manifest, dict):
                unique = manifest.get("UniqueID", "")
                if isinstance(unique, str) and unique.strip():
                    mod_id = unique.strip().lower()
        if mod_id == _DEFAULT_MOD_ID:
            prompt_text = (inp.get("prompt") or "").strip()
            if prompt_text:
                mod_id = slugify_unique_id(
                    prompt_text, prefix="", default=_DEFAULT_MOD_ID
                )

        # Pull weapon list from the definition generator.
        weapons: list[dict[str, object]] = []
        definition_data = prior.get(
            "weapon_definition_definition_generator",
            GeneratorOutput(),
        )
        if isinstance(definition_data, GeneratorOutput):
            weapons_file = definition_data.files.get(
                "assets/weapon_definition/weapons.json"
            )
            if isinstance(weapons_file, dict):
                raw_list = weapons_file.get("weapons", [])
                if isinstance(raw_list, list):
                    weapons = [
                        w for w in raw_list if isinstance(w, dict)
                    ]

        # Defensive fallback: no valid prior outputs ->
        # emit the deterministic 2-weapon fallback list so
        # the content.json still loads.
        if not weapons:
            weapons = _fallback_weapon_list()

        # Build per-weapon Data/Weapons rows and the
        # per-weapon Strings/UI shim entries. The Data/Weapons
        # entries are pipe-delimited strings in the pre-1.6
        # 15-field format Content Patcher expects (see
        # ``_WEAPON_RAW_FIELD_COUNT``); the display name and
        # description are ``[LocalizedText ...]`` tokens resolved
        # from the Strings/UI shim at runtime, matching vanilla
        # weapon rows.
        weapon_entries: dict[str, str] = {}
        strings_entries: dict[str, str] = {}
        for row in weapons:
            token = row.get("ItemId", "")
            if not isinstance(token, str) or not token:
                continue
            display = _sanitize_display_name(row.get("Name", ""))
            if not display:
                # Fallback display name from token so the
                # downstream content.json doesn't crash on
                # an empty string. Strip the
                # ``custom_weapon_`` prefix and title-case
                # the remainder.
                bare = token[
                    len(_WEAPON_TOKEN_PREFIX) + 1:
                ]
                display = (
                    bare.replace("_", " ").title() or token
                )
            description = _sanitize_description(
                row.get("Description", "")
            )
            min_damage = _sanitize_damage(row.get("MinDamage", 0))
            max_damage = _sanitize_damage(
                row.get("MaxDamage", min_damage)
            )
            if max_damage < min_damage:
                max_damage = min_damage
            crit_chance = _sanitize_crit_chance(
                row.get("CritChance", _DEFAULT_CRIT_CHANCE)
            )
            crit_multiplier = _sanitize_crit_multiplier(
                row.get(
                    "CritMultiplier", _DEFAULT_CRIT_MULTIPLIER
                )
            )
            speed = _sanitize_speed(
                row.get("Speed", _DEFAULT_SPEED)
            )
            weapon_type = _sanitize_weapon_type(
                row.get("Type", _DEFAULT_WEAPON_TYPE)
            )
            display_name = row.get("DisplayName", "")
            if (
                not isinstance(display_name, str)
                or not display_name.strip()
            ):
                display_name = (
                    _NAME_KEY_TEMPLATE.format(item_id=token)
                )
            description_key = _DESCRIPTION_KEY_TEMPLATE.format(
                item_id=token
            )

            weapon_entries[token] = "/".join(
                [
                    _sanitize_pipe_field(display),
                    _localized_text_token(description_key),
                    str(min_damage),
                    str(max_damage),
                    "1",
                    str(speed),
                    "0",
                    "0",
                    str(_WEAPON_TYPE_IDS.get(weapon_type, 0)),
                    "-1",
                    "-1",
                    "0",
                    str(crit_chance),
                    str(crit_multiplier),
                    _localized_text_token(display_name),
                ]
            )
            strings_entries[display_name] = display
            strings_entries[description_key] = description

        content: dict[str, object] = {
            "Format": _FORMAT_VERSION,
            "Changes": [
                {
                    "Action": "EditData",
                    "Target": "Data/Weapons",
                    "Entries": weapon_entries,
                },
                {
                    "Action": "EditData",
                    "Target": "Strings/UI",
                    "Entries": strings_entries,
                },
            ],
        }

        out.add_file("content.json", content)

        # Emit manifest.json using the shared helper. This pack has no
        # dedicated ManifestGenerator (the source bundle ships only
        # DefinitionGenerator + ContentJsonGenerator), so the
        # ContentJsonGenerator emits both files. The helper pins the
        # canonical Content Patcher shape and slugifies UniqueID
        # defensively — the LLM may include spaces or special chars.
        manifest_name = fallback_name_from_prompt(
            inp.get("prompt", "weapon_definition"),
            default="Weapon Definition",
        )
        manifest = build_manifest_dict(
            unique_id=mod_id,
            name=manifest_name,
            description=(
                f"Adds {len(weapon_entries)} custom weapon(s) "
                "with per-weapon stats and display strings."
            ),
        )
        out.add_file("manifest.json", manifest)
        out.metadata["mod_id"] = mod_id
        out.metadata["weapon_count"] = len(weapons)
        out.metadata["weapon_entry_count"] = len(weapon_entries)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        """Pin the content.json shape contract.

        content.json is a dict, has ``Format`` and
        ``Changes``, ``Changes[0]["Target"]`` is
        ``"Data/Weapons"``, every ``ItemId`` starts with
        ``"custom_weapon_"``, every ``Data/Weapons`` entry value
        is a pipe-delimited string with the canonical
        ``_WEAPON_RAW_FIELD_COUNT`` fields (so Content Patcher can
        merge it into the structured ``WeaponData`` model), and
        the ``Strings/UI`` change is present.
        """
        errors: list[str] = []
        content = output.files.get("content.json")
        if not content:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json missing"
            )
            return errors
        if not isinstance(content, dict):
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json must be a dict"
            )
            return errors
        if "Format" not in content:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json missing Format field"
            )
        if "Changes" not in content:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json missing Changes field"
            )
            return errors
        changes = content["Changes"]
        if not isinstance(changes, list) or not changes:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json Changes must be a non-empty list"
            )
            return errors

        targets = [
            c.get("Target", "")
            for c in changes
            if isinstance(c, dict)
        ]
        if "Data/Weapons" not in targets:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json missing Data/Weapons change"
            )
        if "Strings/UI" not in targets:
            errors.append(
                "weapon_definition_content_json_generator: "
                "content.json missing Strings/UI change"
            )

        weapons_change = next(
            (
                c for c in changes
                if isinstance(c, dict)
                and c.get("Target") == "Data/Weapons"
            ),
            None,
        )
        if weapons_change is not None:
            entries = weapons_change.get("Entries", {})
            if not isinstance(entries, dict) or not entries:
                errors.append(
                    "weapon_definition_content_json_generator: "
                    "Data/Weapons Entries must be a non-empty "
                    "dict"
                )
            else:
                for key, row in entries.items():
                    if not isinstance(row, str):
                        errors.append(
                            "weapon_definition_content_json_"
                            f"generator: Data/Weapons "
                            f"Entries[{key}] not a string"
                        )
                        continue
                    field_count = row.count("/") + 1
                    if field_count != _WEAPON_RAW_FIELD_COUNT:
                        errors.append(
                            "weapon_definition_content_json_"
                            f"generator: Data/Weapons "
                            f"Entries[{key}] must have "
                            f"{_WEAPON_RAW_FIELD_COUNT} "
                            f"pipe-delimited fields, got "
                            f"{field_count}"
                        )
                    if not key.startswith(
                        f"{_WEAPON_TOKEN_PREFIX}_"
                    ):
                        errors.append(
                            "weapon_definition_content_json_"
                            f"generator: Data/Weapons "
                            f"Entries[{key}] key missing "
                            f"'{_WEAPON_TOKEN_PREFIX}_' "
                            "prefix"
                        )

        # Validate manifest.json (added in v173 — packs without a
        # dedicated ManifestGenerator emit manifest.json alongside
        # content.json via the shared ``build_manifest_dict`` helper).
        manifest = output.files.get("manifest.json")
        if not manifest:
            errors.append(
                "weapon_definition_content_json_generator: "
                "manifest.json missing"
            )
        elif not isinstance(manifest, dict):
            errors.append(
                "weapon_definition_content_json_generator: "
                "manifest.json must be a dict"
            )
        else:
            for required in ("Format", "UniqueID", "Name", "Version"):
                if not manifest.get(required):
                    errors.append(
                        "weapon_definition_content_json_"
                        f"generator: manifest.json missing "
                        f"or empty {required!r} field"
                    )

        return errors


__all__ = [
    "WeaponDefinitionDefinitionGenerator",
    "WeaponDefinitionContentJsonGenerator",
    "WeaponSchema",
    "WeaponListSchema",
    "_MIN_WEAPONS",
    "_MAX_WEAPONS",
    "_DEFAULT_WEAPONS",
    "_WEAPON_TOKEN_PREFIX",
    "_WEAPON_TOKEN_MAX_LEN",
    "_DISPLAY_NAME_MAX_LEN",
    "_DESCRIPTION_MAX_LEN",
    "_ASSET_PATH_MAX_LEN",
    "_MIN_DAMAGE",
    "_MAX_DAMAGE",
    "_MIN_CRIT_CHANCE",
    "_MAX_CRIT_CHANCE",
    "_DEFAULT_CRIT_CHANCE",
    "_MIN_CRIT_MULTIPLIER",
    "_MAX_CRIT_MULTIPLIER",
    "_DEFAULT_CRIT_MULTIPLIER",
    "_MIN_SPEED",
    "_MAX_SPEED",
    "_DEFAULT_SPEED",
    "_WEAPON_TYPES",
    "_DEFAULT_WEAPON_TYPE",
    "_DEFAULT_MOD_ID",
    "_DEFAULT_TEXTURE_TEMPLATE",
    "_WEAPON_RAW_FIELD_COUNT",
    "_WEAPON_TYPE_IDS",
    "_NAME_KEY_TEMPLATE",
    "_DESCRIPTION_KEY_TEMPLATE",
    "_FORMAT_VERSION",
    "_sanitize_weapon_token",
    "_sanitize_damage",
    "_sanitize_crit_chance",
    "_sanitize_crit_multiplier",
    "_sanitize_speed",
    "_sanitize_weapon_type",
    "_sanitize_display_name",
    "_sanitize_description",
    "_sanitize_texture",
    "_sanitize_count",
    "_sanitize_pipe_field",
    "_localized_text_token",
    "_sanitize_weapon_row",
    "_fallback_weapon",
    "_fallback_weapon_list",
]