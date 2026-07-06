"""Custom achievement feature generators for Stardew Valley.

Generates a self-contained Content Patcher mod that adds custom achievements
(``Data/Achievements``) to the game. Three generators cooperate:

1. ``AchievementDefinitionGenerator`` — defines 2-3 new achievements based
   on the user prompt. Each has a unique id, a name, a description, and
   an icon hint.
2. ``AchievementRewardGenerator`` — defines per-achievement rewards
   (gold + optional item + optional friendship bump).
3. ``AchievementContentJsonGenerator`` — assembles the final ``content.json``
   that edits ``Data/Achievements`` and (when a prior manifest exists)
   registers the mod id.

Stardew Valley's vanilla achievement system reads ``Data/Achievements`` at
runtime. Each entry is keyed by a numeric-style id (``0``, ``1`` ... or a
string id, depending on version) and includes ``Name``, ``Description``,
and ``Icon`` fields. We use string ids to avoid collisions with the
vanilla 35+ achievement slots.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

import structlog

from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger(__name__)


# Vanilla SDV has 35 achievements. We start at 100 to avoid clashing with
# any vanilla slot when SMAPI/CP falls back to numeric parsing.
_ACHIEVEMENT_ID_MIN: int = 100
_ACHIEVEMENT_ID_MAX: int = 9999

# Acceptable icon paths. These are the icons Content Patcher can resolve
# against the vanilla tile sheet. We restrict to a safe set so the
# generated ``Icon`` field always points to something loadable.
_VALID_ICON_HINTS: set[str] = {
    "crops", "fishing", "mining", "foraging", "cooking", "crafting",
    "shipping", "monster", "npc", "golden", "walnut", "rabbit", "bear",
    "wizard", "krobus", "yoba", "dark", "clint", "abby", "leah",
}


# Per-pack list-count envelope. The LLM prompt asks for
# "2-3" achievements and a matching reward per achievement; the
# per-reward ``Items`` list is realistically 0-4 entries. We cap
# the Pydantic schema at ``* 2`` so a runaway LLM response with
# hundreds of entries is rejected by validation before
# downstream code wastes memory on it. Mirrors the v82
# ``npc_portrait`` and v83 ``monster_drop`` / v84 ``treasure_hunt``
# and ``currency_system`` convention.
_MAX_ACHIEVEMENTS: int = 3
_MAX_REWARDS: int = 3
_MAX_REWARD_ITEMS: int = 4


class AchievementDefinitionEntry(BaseModel):
    achievement_id: str = Field(validation_alias="AchievementID")
    name: str = Field(validation_alias="Name")
    description: str = Field(default="", validation_alias="Description")
    icon_hint: str = Field(default="crops", validation_alias="IconHint")


class AchievementDefinitionOutput(BaseModel):
    achievements: list[AchievementDefinitionEntry] = Field(
        validation_alias="Achievements",
        max_length=_MAX_ACHIEVEMENTS * 2,
    )


class AchievementRewardEntry(BaseModel):
    achievement_id: str = Field(validation_alias="AchievementID")
    gold: int = Field(default=0, validation_alias="Gold")
    items: list[dict[str, str | int]] = Field(
        default_factory=list,
        validation_alias="Items",
        max_length=_MAX_REWARD_ITEMS * 2,
    )
    friendship_points: int = Field(default=0, validation_alias="FriendshipPoints")
    friendship_target: str | None = Field(default=None, validation_alias="FriendshipTarget")


class AchievementRewardOutput(BaseModel):
    rewards: list[AchievementRewardEntry] = Field(
        validation_alias="Rewards",
        max_length=_MAX_REWARDS * 2,
    )


def _sanitize_achievement_id(raw: str | int | None) -> str:
    """Return a safe string achievement id.

    Stardew accepts either numeric or short string ids in
    ``Data/Achievements``. We pick a numeric slot in the 100-9999 range
    derived from the (sanitized) input, falling back to a stable default.
    """
    if raw is None:
        return "100"
    if isinstance(raw, int):
        return _clamp_id(raw)
    if isinstance(raw, str):
        cleaned = "".join(c for c in raw if c.isalnum() or c == "_")
        if not cleaned:
            return "100"
        # If the cleaned id is all-digits, clamp it into the safe range.
        if cleaned.isdigit():
            return _clamp_id(int(cleaned))
        # Otherwise, hash the non-numeric id into a stable numeric slot.
        return _clamp_id(_stable_hash_to_int(cleaned))
    return "100"


def _clamp_id(value: int) -> str:
    if value < _ACHIEVEMENT_ID_MIN:
        return str(_ACHIEVEMENT_ID_MIN)
    if value > _ACHIEVEMENT_ID_MAX:
        return str(_ACHIEVEMENT_ID_MAX)
    return str(value)


def _stable_hash_to_int(text: str) -> int:
    """Deterministic small int from a string. FNV-1a 32-bit, masked to range."""
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _normalize_icon_hint(hint: str | None) -> str:
    """Map an LLM-supplied icon hint to one of the known safe icon paths."""
    if not isinstance(hint, str):
        return "crops"
    cleaned = hint.strip().lower()
    return cleaned if cleaned in _VALID_ICON_HINTS else "crops"


class AchievementDefinitionGenerator(BaseGenerator):
    """Define 2-3 custom achievements for a Stardew Valley mod."""

    name = "achievement_definition_generator"
    phase = "achievements"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create custom achievements for a Stardew Valley mod based on: "{inp["prompt"]}"

Generate 2-3 unique achievements. For each achievement provide:
- AchievementID: a short identifier (alphanumeric, e.g. "mossy_milestone", "comrade_500")
- Name: short title (2-5 words)
- Description: one sentence describing what the player must do (under 100 chars)
- IconHint: one of the allowed icon keywords — choose the one that best
  represents the achievement: crops, fishing, mining, foraging, cooking,
  crafting, shipping, monster, npc, golden, walnut, rabbit, bear, wizard,
  krobus, yoba, dark, clint, abby, leah

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, AchievementDefinitionOutput,
                system=llm_system_prompt(),
                max_tokens=2048,
            )
            parsed = AchievementDefinitionOutput(**result)
            ach_dicts: list[dict] = []
            for a in parsed.achievements:
                ach_dicts.append({
                    "AchievementID": _sanitize_achievement_id(a.achievement_id),
                    "Name": (a.name or "Custom Achievement").strip(),
                    "Description": (a.description or "").strip(),
                    "IconHint": _normalize_icon_hint(a.icon_hint),
                })
            if not ach_dicts:
                raise ValueError("no achievements produced")
            out.add_file("assets/achievements/achievements.json", {"achievements": ach_dicts})
            out.metadata["achievement_count"] = len(ach_dicts)
            out.metadata["first_achievement_id"] = ach_dicts[0]["AchievementID"]
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("achievement_definition_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("assets/achievements/achievements.json", {
                "achievements": [
                    {
                        "AchievementID": "100",
                        "Name": "First Harvest",
                        "Description": "Harvest your very first crop.",
                        "IconHint": "crops",
                    },
                    {
                        "AchievementID": "101",
                        "Name": "Steady Hand",
                        "Description": "Catch 10 fish of any kind.",
                        "IconHint": "fishing",
                    },
                ]
            })
            out.metadata["achievement_count"] = 2
            out.metadata["first_achievement_id"] = "100"
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        data = output.files.get("assets/achievements/achievements.json")
        if not data:
            errors.append("achievement_definition_generator: assets/achievements/achievements.json missing")
            return errors
        if not isinstance(data, dict):
            errors.append("achievement_definition_generator: achievements.json must be a dict")
            return errors
        achievements = data.get("achievements")
        if not isinstance(achievements, list) or not achievements:
            errors.append("achievement_definition_generator: achievements list missing or empty")
            return errors
        for a in achievements:
            if not isinstance(a, dict):
                errors.append("achievement_definition_generator: each achievement must be a dict")
                continue
            if "AchievementID" not in a or "Name" not in a:
                errors.append("achievement_definition_generator: missing AchievementID or Name")
        return errors


class AchievementRewardGenerator(BaseGenerator):
    """Per-achievement rewards: gold, items, friendship bump."""

    name = "achievement_reward_generator"
    phase = "achievements"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        defn = prior.get("achievement_definition_generator", GeneratorOutput())
        ach_file = defn.files.get("assets/achievements/achievements.json", {})
        ach_list = ach_file.get("achievements", []) if isinstance(ach_file, dict) else []

        prompt = f'''Create rewards for custom achievements in a Stardew Valley mod based on: "{inp["prompt"]}"

Achievements for context:
{ach_list if ach_list else "(generate rewards for 2 default achievements)"}

For each achievement, design a fair reward:
- Standard achievement: 500-3000 gold, optional small item
- Major achievement: 5000-15000 gold, optional unique item
- Optional friendship bump: 0-500 points with a target NPC (can be null)

For each reward provide:
- AchievementID: must match one of the ids above
- Gold: 0-15000
- Items: list of {{"Name": "<item>", "Count": 1-5}} (can be empty)
- FriendshipPoints: 0-500
- FriendshipTarget: NPC name or null

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, AchievementRewardOutput,
                system=llm_system_prompt(),
                max_tokens=2048,
            )
            parsed = AchievementRewardOutput(**result)
            reward_dicts: list[dict] = []
            for r in parsed.rewards:
                aid = _sanitize_achievement_id(r.achievement_id)
                reward_dicts.append({
                    "AchievementID": aid,
                    "Gold": max(0, int(r.gold)),
                    "Items": [i for i in r.items if isinstance(i, dict)],
                    "FriendshipPoints": max(0, int(r.friendship_points)),
                    "FriendshipTarget": r.friendship_target or "",
                })
            out.add_file("assets/achievements/rewards.json", {"rewards": reward_dicts})
            out.metadata["reward_count"] = len(reward_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("achievement_reward_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("assets/achievements/rewards.json", {
                "rewards": [
                    {
                        "AchievementID": "100",
                        "Gold": 1000,
                        "Items": [{"Name": "Parsnip Seeds", "Count": 10}],
                        "FriendshipPoints": 0,
                        "FriendshipTarget": "",
                    },
                    {
                        "AchievementID": "101",
                        "Gold": 2500,
                        "Items": [{"Name": "Bamboo Pole", "Count": 1}],
                        "FriendshipPoints": 100,
                        "FriendshipTarget": "Willy",
                    },
                ]
            })
            out.metadata["reward_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        data = output.files.get("assets/achievements/rewards.json")
        if not data:
            errors.append("achievement_reward_generator: assets/achievements/rewards.json missing")
            return errors
        if not isinstance(data, dict):
            errors.append("achievement_reward_generator: rewards.json must be a dict")
        return errors


class AchievementContentJsonGenerator(BaseGenerator):
    """Assemble content.json that edits Data/Achievements."""

    name = "achievement_content_json_generator"
    phase = "achievements"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        if isinstance(manifest_data, dict):
            mod_id = str(manifest_data.get("UniqueID", "Custom.Achievements")).lower()
        else:
            mod_id = "Custom.Achievements"

        defn = prior.get("achievement_definition_generator", GeneratorOutput())
        reward = prior.get("achievement_reward_generator", GeneratorOutput())

        changes: list[dict] = []

        # 1. Data/Achievements — one entry per achievement with Name, Description,
        #    Icon, and (if a reward exists) Rewards. Vanilla SDV looks up
        #    achievements by numeric string id under this asset.
        ach_file = "assets/achievements/achievements.json"
        ach_data = defn.files.get(ach_file, {})
        if isinstance(ach_data, dict) and isinstance(ach_data.get("achievements"), list):
            ach_entries: dict[str, dict] = {}
            for a in ach_data["achievements"]:
                if not isinstance(a, dict):
                    continue
                aid = _sanitize_achievement_id(a.get("AchievementID"))
                if not aid:
                    continue
                ach_entries[aid] = {
                    "Name": a.get("Name", "Custom Achievement"),
                    "Description": a.get("Description", ""),
                    "Icon": _normalize_icon_hint(a.get("IconHint")),
                }
            if ach_entries:
                changes.append({
                    "Action": "EditData",
                    "Target": "Data/Achievements",
                    "Entries": ach_entries,
                })

        # 2. Merge rewards into the same Data/Achievements table under the
        #    per-achievement "Rewards" field. Content Patcher handles
        #    additive Fields on the same Target.
        reward_file = "assets/achievements/rewards.json"
        reward_data = reward.files.get(reward_file, {})
        if isinstance(reward_data, dict) and isinstance(reward_data.get("rewards"), list):
            reward_entries: dict[str, dict] = {}
            for r in reward_data["rewards"]:
                if not isinstance(r, dict):
                    continue
                aid = _sanitize_achievement_id(r.get("AchievementID"))
                if not aid:
                    continue
                reward_entries[aid] = {
                    "Money": max(0, int(r.get("Gold", 0))),
                    "Items": [i for i in r.get("Items", []) if isinstance(i, dict)],
                    "FriendshipPoints": max(0, int(r.get("FriendshipPoints", 0))),
                }
                # Only include a target NPC when one is set
                target = r.get("FriendshipTarget", "")
                if isinstance(target, str) and target.strip():
                    reward_entries[aid]["FriendshipTarget"] = target.strip()
            if reward_entries:
                changes.append({
                    "Action": "EditData",
                    "Target": "Data/Achievements",
                    "Entries": reward_entries,
                    "Fields": "Rewards",
                })

        # 3. Friendly Strings/UI registration so the achievement popup reads
        #    "New Achievement!" with the mod's localised name. CP will only
        #    apply this when the mod is enabled.
        if changes:
            changes.append({
                "Action": "EditData",
                "Target": "Data/Strings/UI",
                "Entries": {
                    f"Achievement_{mod_id}": "New Achievement unlocked!",
                },
                "When": {"HasMod": mod_id},
            })

        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": changes,
        })
        out.metadata["mod_id"] = mod_id
        out.metadata["changes_count"] = len(changes)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors: list[str] = []
        content = output.files.get("content.json")
        if not content:
            errors.append("achievement_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("achievement_content_json_generator: content.json must be a dict")
            return errors
        if "Changes" not in content:
            errors.append("achievement_content_json_generator: Changes key missing")
        return errors