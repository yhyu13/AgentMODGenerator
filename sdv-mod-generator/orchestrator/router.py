"""Intent routing: keyword matching → generator list + hint."""
from typing import TypedDict


class RoutingHint(TypedDict):
    """Hint returned by router to orchestrator."""
    phase: str
    generators: list[str]
    execution_order: list[str]
    dependencies: dict[str, list[str]]


# Source of truth: keyword → ordered generator list
FEATURE_TO_GENERATORS: dict[str, list[str]] = {
    # P0 texture
    "texture": ["texture_generator"],
    "sprite": ["texture_generator"],
    "image": ["texture_generator"],
    # P1 shop channel (manifest should always come first)
    "shop": ["manifest_generator", "shop_item_pool_generator"],
    "store": ["manifest_generator", "shop_item_pool_generator"],
    "buy": ["manifest_generator", "shop_item_pool_generator"],
    "sell": ["manifest_generator", "shop_item_pool_generator"],
    "tv shopping": [
        "manifest_generator",
        "shop_item_pool_generator",
        "tv_channel_generator",
        "mail_system_generator",
        "item_sprites_generator",
        "ui_assets_generator",
        "catalog_preview_generator",
        "realism_damage_generator",
        "trigger_logic_generator",
        "config_schema_generator",
    ],
    "tv": ["tv_channel_generator"],
    "channel": ["tv_channel_generator"],
    "broadcast": ["tv_channel_generator"],
    "mail": ["mail_system_generator"],
    "letter": ["mail_system_generator"],
    "delivery": ["mail_system_generator"],
}

FEATURE_TO_PHASE: dict[str, str] = {
    "texture": "p0_texture",
    "sprite": "p0_texture",
    "image": "p0_texture",
    "shop": "p1_shop_channel",
    "store": "p1_shop_channel",
    "buy": "p1_shop_channel",
    "sell": "p1_shop_channel",
    "tv shopping": "p1_shop_channel",
    "tv": "p1_shop_channel",
    "channel": "p1_shop_channel",
    "broadcast": "p1_shop_channel",
    "mail": "p1_shop_channel",
    "letter": "p1_shop_channel",
    "delivery": "p1_shop_channel",
}

# Dependencies between generators (for future use in P3+)
# Currently unused — node_generate runs generators in execution_order sequentially
FEATURE_DEPENDENCIES: dict[str, dict[str, list[str]]] = {
    "tv shopping": {
        "shop_item_pool_generator": ["manifest_generator"],
        "catalog_preview_generator": ["shop_item_pool_generator"],
        "tv_channel_generator": ["manifest_generator", "catalog_preview_generator"],
        "mail_system_generator": ["tv_channel_generator"],
        "trigger_logic_generator": ["tv_channel_generator"],
        "realism_damage_generator": ["trigger_logic_generator"],
        "config_schema_generator": ["trigger_logic_generator"],
    },
}

DEFAULT_GENERATORS = ["manifest_generator", "shop_item_pool_generator"]


def route(prompt: str) -> tuple[str, RoutingHint]:
    """Match keywords in prompt to generators and phase.

    Returns:
        tuple of (phase, RoutingHint)
    """
    prompt_lower = prompt.lower()
    matched_features: set[str] = set()
    matched_generators: list[str] = []

    for keyword, generators in FEATURE_TO_GENERATORS.items():
        if keyword in prompt_lower:
            matched_features.add(keyword)
            for g in generators:
                if g not in matched_generators:
                    matched_generators.append(g)

    primary_feature = next(iter(matched_features), None) if matched_features else None

    if not matched_generators:
        matched_generators = DEFAULT_GENERATORS.copy()
        matched_phase = "p1_shop_channel"
    else:
        matched_phase = FEATURE_TO_PHASE.get(primary_feature, "p1_shop_channel") if primary_feature else "p1_shop_channel"

    hint: RoutingHint = {
        "phase": matched_phase,
        "generators": matched_generators,
        "execution_order": matched_generators,
        "dependencies": FEATURE_DEPENDENCIES.get(primary_feature, {}) if primary_feature else {},
    }
    return matched_phase, hint
