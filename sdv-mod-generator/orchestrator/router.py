"""Intent routing: detect game → detect feature → return generator list.

Game detection happens first (keyword or LLM), then feature/phase routing
within the detected game pack.
"""
from typing import TypedDict

import structlog

logger = structlog.get_logger()


class RoutingHint(TypedDict):
    """Hint returned by router to orchestrator."""
    game: str
    phase: str
    generators: list[str]
    execution_order: list[str]
    dependencies: dict[str, list[str]]


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
        "crafting": "custom_crafting",
        "recipe": "custom_crafting",
        "cooking": "custom_crafting",
    },
}


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
    prompt_lower = prompt.lower()

    phase_map = _PHASE_BY_KEYWORD.get(game_id, {})
    matched_generators: list[str] = []
    matched_phase: str | None = None
    best_keyword_len = 0

    for keyword, phase in phase_map.items():
        if keyword in prompt_lower and len(keyword) > best_keyword_len:
            best_keyword_len = len(keyword)
            matched_phase = phase

    if matched_phase is None:
        matched_phase = phase_map.get("shop", "shop_channel")

    try:
        from generators.core import get_game_pack
        pack = get_game_pack(game_id)
        if pack is None:
            logger.warning("router.pack_not_found", game=game_id, phase=matched_phase)
            matched_generators = _default_generators_for_phase(matched_phase)
        elif matched_phase not in pack.list_phases():
            logger.warning("router.phase_not_in_pack", game=game_id, phase=matched_phase)
            matched_generators = _default_generators_for_phase(matched_phase)
        else:
            pg = pack.get_generators(matched_phase)
            matched_generators = pg.execution_order.copy()
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        logger.warning("router.pack_fallback", game=game_id, phase=matched_phase, error=str(exc))
        matched_generators = _default_generators_for_phase(matched_phase)

    hint: RoutingHint = {
        "game": game_id,
        "phase": matched_phase,
        "generators": matched_generators,
        "execution_order": matched_generators,
        "dependencies": {},
    }
    logger.info("router.routed", game=game_id, phase=matched_phase, generators=matched_generators)
    return matched_phase, hint


def _default_generators_for_phase(phase: str) -> list[str]:
    if phase == "texture":
        return ["texture_generator"]
    if phase == "shop_channel":
        return [
            "manifest_generator", "shop_item_pool_generator", "tv_channel_generator",
            "mail_system_generator", "item_sprites_generator", "ui_assets_generator",
            "catalog_preview_generator", "realism_damage_generator",
            "trigger_logic_generator", "config_schema_generator",
            "content_json_generator",
        ]
    return []