"""Intent routing: detect game → detect feature → return generator list.

Game detection happens first (keyword or LLM), then feature/phase routing
within the detected game pack.
"""
from typing import TypedDict

import structlog

# Bind a module-level logger so every structlog event carries the
# fully-qualified module name (``orchestrator.router``) by default.
# Other modules in this codebase do the same (see ``storage.postgres``
# ported in round 8); the bare ``structlog.get_logger()`` form (no
# name argument) was a minor convention drift — addressed in v27
# Blue for grep consistency. The 7-line comment matches the source's
# pattern in ``docs/_source_router.py.txt`` lines 10-15.
logger = structlog.get_logger(__name__)


class RoutingHint(TypedDict):
    """Hint returned by router to orchestrator."""
    game: str
    phase: str
    generators: list[str]
    execution_order: list[str]
    dependencies: dict[str, list[str]]
    # v27 Blue: confidence + matched_keyword round-tripped from the
    # longest-keyword-wins loop so the orchestrator (and the future
    # ``GET /v1/router/diagnose`` endpoint) can show *why* a prompt
    # routed to a particular phase. ``confidence`` is 0.0 for the
    # default fallback (no keyword matched) and 1.0 for matches of
    # ``~16`` chars or longer (the longest real keyword in the maps).
    # ``matched_keyword`` is the literal keyword string that won the
    # longest-match scan, or ``""`` for the default fallback. Both
    # fields are additive — older callers reading the first 5 keys
    # are unaffected.
    confidence: float
    matched_keyword: str


_GAME_KEYWORDS: dict[str, list[str]] = {
    "stardew_valley": [
        "stardew", "stardew valley", "sdv",
        "tv shopping", "tv channel", "shop", "store", "buy", "sell",
        "npc schedule", "npc routine", "npc event", "npc dialogue",
        "crop", "farm", "season", "weather",
        "texture", "sprite", "image mod",
        "crafting", "recipe", "cooking",
        "mine", "combat", "monster",
    ],
    "minecraft": [
        "minecraft", "crafting table", "redstone", "bukkit", "spigot",
        "forge", "fabric", "datapack", "resourcepack",
    ],
    "skyrim": [
        "skyrim", "elder scrolls", "dragonborn", "quests", "npcs",
    ],
}

_PHASE_BY_KEYWORD: dict[str, dict[str, str]] = {
    "stardew_valley": {
        "texture": "texture",
        "sprite": "texture",
        "image": "texture",
        "shop": "shop_channel",
        "store": "shop_channel",
        "buy": "shop_channel",
        "sell": "shop_channel",
        "tv shopping": "shop_channel",
        "tv": "shop_channel",
        "channel": "shop_channel",
        "broadcast": "shop_channel",
        "mail": "shop_channel",
        "letter": "shop_channel",
        "delivery": "shop_channel",
        "npc schedule": "npc_schedule",
        "npc routine": "npc_schedule",
        "npc event": "event_mod",
        "npc dialogue": "npc_schedule",
        "daily schedule": "npc_schedule",
        "schedule": "npc_schedule",
        "festival": "event_mod",
        "event": "event_mod",
        "celebration": "event_mod",
        "fair": "event_mod",
        "crafting": "custom_crafting",
        "recipe": "custom_crafting",
        "cooking": "custom_crafting",
        "farm expansion": "farm_expansion",
        "building": "farm_expansion",
        "warp": "farm_expansion",
        "map edit": "farm_expansion",
        "new area": "farm_expansion",
        "achievement": "achievements",
        "achievements": "achievements",
        "badge": "achievements",
        "trophy": "achievements",
        "milestone": "achievements",
        "weapon": "weapon_definition",
        "weapons": "weapon_definition",
        "sword": "weapon_definition",
        "dagger": "weapon_definition",
        "club": "weapon_definition",
        "slingshot": "weapon_definition",
        "weapon definition": "weapon_definition",
        "custom weapon": "weapon_definition",
        "tool": "tool_definition",
        "tools": "tool_definition",
        "pickaxe": "tool_definition",
        "axe": "tool_definition",
        "hoe": "tool_definition",
        "watering can": "tool_definition",
        "fishing rod": "tool_definition",
        "tool definition": "tool_definition",
        "custom tool": "tool_definition",
    },
}

#: Explicit festival/event-type words that pin a prompt to ``event_mod``
#: even when a weather keyword is present. Without this guard the
#: weather-priority override steals prompts like ``"a festival where it
#: snows candy"`` into ``weather_event``.
_EVENT_TYPE_KEYWORDS: tuple[str, ...] = (
    "festival", "celebration", "fair", "parade", "carnival", "gathering",
)

#: Concepts with NO generator phase in this project. When a prompt falls
#: through the phase scan and mentions one of these, it routes to the
#: ``no_support`` sentinel phase so the pipeline fails with a clear
#: "unsupported_request" error instead of silently producing an unrelated
#: ``shop_channel`` mod (e.g. a quest/fish/monster request became a TV
#: shopping channel).
_IMPOSSIBLE_KEYWORDS: tuple[str, ...] = (
    "c#", "c sharp", "csharp", "code mod", ".dll", "dll mod",
    "source code", "framework mod", "custom framework", "smapi code",
)


def detect_game(prompt: str) -> str:
    """Detect which game the prompt is about.

    Uses keyword matching first, falls back to LLM classification,
    defaults to 'stardew_valley'.
    """
    prompt_lower = prompt.lower()
    for game_id, keywords in _GAME_KEYWORDS.items():
        for kw in keywords:
            if kw in prompt_lower:
                logger.info("router.game_detected", game=game_id, keyword=kw)
                return game_id

    return "stardew_valley"


def route(prompt: str) -> tuple[str, RoutingHint]:
    """Match a prompt to game, phase, and generators.

    Two-stage routing:
    1. Detect game (keyword → LLM → stardew_valley fallback)
    2. Detect phase + generators within the game pack

    Returns:
        tuple of (phase, RoutingHint with game included)
    """
    game_id = detect_game(prompt)
    # Boundary guard: only route to a game that has a registered pack.
    # The keyword tables include other games (minecraft/skyrim) whose
    # keywords (e.g. ``forge``) can appear in Stardew prompts; routing to
    # a game with no pack made the pipeline hard-fail with "Unknown game".
    try:
        from generators.core import get_game_pack
        if get_game_pack(game_id) is None:
            logger.warning("router.unknown_game_fallback", game=game_id)
            game_id = "stardew_valley"
    except ImportError:
        pass
    prompt_lower = prompt.lower()

    phase_map = _PHASE_BY_KEYWORD.get(game_id, {})
    matched_generators: list[str] = []
    matched_phase: str | None = None
    matched_keyword: str = ""
    best_keyword_len = 0

    for keyword, phase in phase_map.items():
        if keyword in prompt_lower and len(keyword) > best_keyword_len:
            best_keyword_len = len(keyword)
            matched_phase = phase
            matched_keyword = keyword

    # Weather-event priority override: when the prompt contains BOTH a
    # weather keyword (rain/storm/snow/wind/weather/buff) AND the generic
    # ``event`` word, prefer ``weather_event`` over ``event_mod``. Without
    # this, the longest-keyword-wins tie between ``"event"`` (5 chars) and
    # ``"storm"``/``"rain"`` (4-5 chars) resolves to ``event_mod`` purely
    # by dict-insertion order, even though semantically the user wants a
    # weather event (e.g. ``"add a rain storm event"``). Festival/event
    # prompts without any weather keyword still resolve to ``event_mod``
    # via the main loop above.
    if matched_phase == "event_mod" and any(
        k in prompt_lower for k in ("rain", "storm", "snow", "wind", "weather", "buff")
    ) and not any(k in prompt_lower for k in _EVENT_TYPE_KEYWORDS):
        matched_phase = "weather_event"
        # ``matched_keyword`` stays as the longest original match
        # (the ``"event"`` literal) so the v27 confidence / diagnose
        # surface can render "the route was overridden after a 5-char
        # 'event' match" rather than the synthetic 12-char
        # ``"weather_event"`` phase name. The phase changed, the
        # trigger didn't.
        matched_keyword = "event"

    is_fallback = matched_phase is None
    if is_fallback:
        impossible_kw = next(
            (kw for kw in _IMPOSSIBLE_KEYWORDS if kw in prompt_lower), None
        )
        if impossible_kw:
            matched_phase = "no_support"
            matched_keyword = impossible_kw
        else:
            # Hybrid routing: novel/unknown concepts go to the general
            # LLM CP-author phase instead of silently falling back to
            # shop_channel. Only explicitly-impossible demands (C# code
            # mods, custom frameworks) keep the no_support sentinel.
            matched_phase = "general_author"
            matched_keyword = ""

    # v27 Blue: confidence heuristic based on matched keyword length.
    # The longest real keyword in the maps is ~16 chars ("seasonal
    # festival", "map edit"); matches that long (or longer) get full
    # confidence (1.0). Single-word matches (3-4 chars) score low
    # (0.2-0.25) so the orchestrator can decide whether to ask the
    # user for clarification. Fallback (no keyword matched) is 0.0.
    # The 16-char ceiling is intentionally a *floor* of 1.0 — we
    # cap at 1.0 because no real keyword is longer, but future
    # longer keywords should not blow past the API contract.
    if is_fallback or best_keyword_len == 0:
        confidence: float = 0.0
    else:
        confidence = min(1.0, round(best_keyword_len / 16.0, 2))

    try:
        from generators.core import get_game_pack
        pack = get_game_pack(game_id)
        if pack is None:
            logger.warning("router.pack_not_found", game=game_id, phase=matched_phase)
            matched_generators = _default_generators_for_phase(matched_phase)
        elif matched_phase == "no_support":
            matched_generators = []
        elif matched_phase not in pack.list_phases():
            logger.warning("router.phase_not_in_pack", game=game_id, phase=matched_phase)
            matched_generators = _default_generators_for_phase(matched_phase)
        else:
            pg = pack.get_generators(matched_phase)
            matched_generators = pg.execution_order.copy()
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        # v27 Blue: surface ``error_type`` (exception class name) on
        # the pack-fallback warning so log aggregators can group
        # router fallbacks by exception class without parsing the
        # ``error`` string. The string form is preserved for
        # backwards compatibility with dashboards that grep on it.
        logger.warning(
            "router.pack_fallback",
            game=game_id,
            phase=matched_phase,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        matched_generators = _default_generators_for_phase(matched_phase)

    hint: RoutingHint = {
        "game": game_id,
        "phase": matched_phase,
        "generators": matched_generators,
        "execution_order": matched_generators,
        "dependencies": {},
        "confidence": confidence,
        "matched_keyword": matched_keyword,
    }
    logger.info(
        "router.routed",
        game=game_id,
        phase=matched_phase,
        generators=matched_generators,
        confidence=confidence,
        matched_keyword=matched_keyword,
    )
    return matched_phase, hint


def _default_generators_for_phase(phase: str) -> list[str]:
    """Resolve a phase string to its defence-in-depth generator-name list.

    This is the fallback that :func:`route` consults when the upstream
    pack-based resolver (``StardewValleyPack.get_generators(phase)``) is
    unavailable or does not know the phase. Every phase advertised by
    ``StardewValleyPack.list_phases()`` must have a matching
    ``if phase == "..."`` arm here, and an UNKNOWN phase (no matching
    ``if`` arm) returns ``[]`` and emits the
    ``router.default_generators.unknown`` WARNING so an operator can
    correlate a downstream "pipeline generated zero files" failure back
    to the specific phase string that fell through.

    Logging:
    * Matched phase: no log call here (the matched-phase success path
      is logged at the :func:`route` call site).
    * Unknown phase: emits ``router.default_generators.unknown``
      (WARNING, single ``phase`` snake_case field) immediately before
      returning ``[]``. WARNING (not INFO) because an unknown phase is
      always actionable — it indicates either a typo'd phase string or
      a pack drift (a phase added to the pack without the parallel
      fallback arm here).

    Mirrors the source's
    ``docs/_source_router.py.txt`` lines 1594-2450 v99 hardening.
    Master has only 5 phase arms today; the source has 60+ and a
    v99 telemetry contract on the silent-fallthrough path. This port
    keeps master's 5 arms and adds the v99 WARNING contract; the
    remaining 55+ arms remain out of scope until the corresponding
    packs land (they each have a pack-registration dependency that
    the broader P3-P5 stack needs to provide).
    """
    if phase == "no_support":
        return []
    if phase == "general_author":
        return ["general_author_generator"]
    if phase == "texture":
        return ["texture_generator"]
    if phase == "npc_schedule":
        return [
            "manifest_generator",
            "npc_schedule_generator",
            "npc_dialogue_generator",
            "npc_gift_taste_generator",
            "npc_content_json_generator",
        ]
    if phase == "shop_channel":
        return [
            "manifest_generator", "shop_item_pool_generator", "tv_channel_generator",
            "mail_system_generator", "item_sprites_generator", "ui_assets_generator",
            "catalog_preview_generator", "realism_damage_generator",
            "trigger_logic_generator", "config_schema_generator",
            "content_json_generator",
        ]
    if phase == "event_mod":
        return [
            "festival_schedule_generator",
            "festival_shop_generator",
            "festival_map_generator",
            "festival_dialogue_generator",
            "festival_mail_generator",
            "festival_content_json_generator",
        ]
    if phase == "custom_crafting":
        return [
            "crafting_recipe_generator",
            "cooking_recipe_generator",
            "crafting_content_json_generator",
        ]
    if phase == "farm_expansion":
        return [
            "manifest_generator",
            "building_generator",
            "warp_point_generator",
            "map_edit_generator",
            "farm_expansion_content_json_generator",
        ]
    if phase == "weather_event":
        return [
            "weather_manifest_generator",
            "weather_event_generator",
            "weather_npc_dialogue_generator",
            "weather_buff_generator",
            "weather_mail_generator",
            "weather_content_json_generator",
        ]
    if phase == "achievements":
        return [
            "achievement_definition_generator",
            "achievement_reward_generator",
            "achievement_content_json_generator",
        ]
    # v22 Blue (port from source v99): unknown-phase silent-failure
    # gap. The phase did not match any of the ``if phase == "..."``
    # arms above, so we are about to return an empty list. Emit a
    # canonical ``router.default_generators.unknown`` WARNING so an
    # operator can correlate a "pipeline generated zero files"
    # downstream failure back to the specific phase string that fell
    # through. The single ``phase`` field is sufficient — the call
    # site (``route()``) already logs the full routing context
    # (``router.pack_not_found`` / ``router.phase_not_in_pack`` /
    # ``router.pack_fallback``) so a log query can pivot off this
    # WARNING to surface the offending phase. WARNING (not INFO)
    # because an unknown phase is always actionable — it indicates
    # either a typo'd phase string or a pack drift (a phase added
    # to the pack without the parallel fallback arm here).
    logger.warning("router.default_generators.unknown", phase=phase)
    return []