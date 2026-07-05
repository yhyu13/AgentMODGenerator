"""Weather-based event feature generators for Stardew Valley.

Generates custom weather events that trigger on specific weather conditions,
including rain bonuses, storm events, snow activities, and weather-dependent
NPC dialogue.
"""
from pydantic import BaseModel, Field, ValidationError

import structlog
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger(__name__)


class WeatherEventEntry(BaseModel):
    event_name: str = Field(validation_alias="EventName")
    weather_condition: str = Field(validation_alias="WeatherCondition")
    season: str | None = Field(default=None, validation_alias="Season")
    description: str = Field(default="", validation_alias="Description")
    effects: list[dict[str, str | int | float]] = Field(default_factory=list, validation_alias="Effects")


# Per-pack list-count envelope. The LLM prompts ask for
# "3-5" weather events, "4-6" NPC dialogue lines, "3-5"
# buffs, and "2-3" mails. We cap the Pydantic schema at
# ``* 2`` so a runaway LLM response with hundreds of
# entries is rejected by validation before downstream code
# wastes memory on it. Mirrors the v82 ``npc_portrait``,
# v83 ``monster_drop``, v84 ``treasure_hunt`` /
# ``currency_system``, v85 ``sign_editor`` /
# ``achievements`` / ``books``, and v86 ``custom_crafting`` /
# ``fruit_tree`` convention.
_MAX_WEATHER_EVENTS: int = 5
_MAX_NPC_DIALOGUE: int = 6
_MAX_BUFFS: int = 5
_MAX_MAILS: int = 3


class WeatherEventOutput(BaseModel):
    events: list[WeatherEventEntry] = Field(
        validation_alias="Events",
        max_length=_MAX_WEATHER_EVENTS * 2,
    )


class WeatherNPCDialogueEntry(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    weather_condition: str = Field(validation_alias="WeatherCondition")
    dialogue: str = Field(validation_alias="Dialogue")


class WeatherNPCDialogueOutput(BaseModel):
    dialogues: list[WeatherNPCDialogueEntry] = Field(
        validation_alias="Dialogues",
        max_length=_MAX_NPC_DIALOGUE * 2,
    )


class WeatherBuffEntry(BaseModel):
    buff_name: str = Field(validation_alias="BuffName")
    weather_condition: str = Field(validation_alias="WeatherCondition")
    stat: str = Field(validation_alias="Stat")
    value: int = Field(validation_alias="Value")
    duration: int = Field(default=300, validation_alias="Duration")


class WeatherBuffOutput(BaseModel):
    buffs: list[WeatherBuffEntry] = Field(
        validation_alias="Buffs",
        max_length=_MAX_BUFFS * 2,
    )


class WeatherMailEntry(BaseModel):
    mail_key: str = Field(validation_alias="MailKey")
    subject: str = Field(validation_alias="Subject")
    body: str = Field(validation_alias="Body")
    weather_condition: str = Field(validation_alias="WeatherCondition")


class WeatherMailOutput(BaseModel):
    mails: list[WeatherMailEntry] = Field(
        validation_alias="Mails",
        max_length=_MAX_MAILS * 2,
    )


def _sanitize_weather_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c == "_") or "WeatherEvent"


# v44 — shared prior-envelope helpers (mirrors the v39-v43 refactor
# pattern applied to pet_breed, event_mod, seasonal_festival, and
# audio_pack). The weather_event pack originally had 5 inline
# ``prior.get(...)`` chains inside ``WeatherContentJsonGenerator``;
# refactoring them out collapses 8 lines of defensive unwrapping
# into 5 single-line helper calls and makes the malformed-input
# behaviour unit-testable in isolation.


def _get_prior_generator(prior_outputs: object, name: str) -> GeneratorOutput:
    """Defensive slot-extraction for the prior_outputs envelope.

    Mirrors the contract introduced in v41 ``event_mod`` and reused in
    v42 ``seasonal_festival`` and v43 ``audio_pack``: every malformed
    layer (non-dict prior, missing slot, non-Generator slot) collapses
    silently to an empty :class:`GeneratorOutput` so downstream
    generators can iterate without ``isinstance`` guards.

    Args:
        prior_outputs: The ``inp["prior_outputs"]`` value (typed as
            ``object`` because defensive helpers must accept any shape).
        name: The generator name to look up (e.g.
            ``"weather_event_generator"``).

    Returns:
        The matching :class:`GeneratorOutput` if the slot is well-formed,
        otherwise an empty :class:`GeneratorOutput` (with empty
        ``files`` and ``metadata`` dicts).
    """
    if not isinstance(prior_outputs, dict):
        return GeneratorOutput()
    slot = prior_outputs.get(name)
    if not isinstance(slot, GeneratorOutput):
        return GeneratorOutput()
    return slot


def _extract_mod_id_from_manifest_prior(
    prior_outputs: object,
    default: str = "WeatherMod",
) -> str:
    """Read ``UniqueID`` from the upstream ``manifest_generator`` slot.

    Lowercases the value (Content Patcher convention) and falls back to
    ``default`` (default ``"WeatherMod"``) when the manifest slot is
    missing or malformed. The defensive collapse matches the inline
    pattern the v43 code path used to do by hand.
    """
    gen = _get_prior_generator(prior_outputs, "manifest_generator")
    files = getattr(gen, "files", None)
    if not isinstance(files, dict):
        return default
    manifest = files.get("manifest.json")
    if not isinstance(manifest, dict):
        return default
    raw = manifest.get("UniqueID", default)
    return str(raw).lower() if isinstance(raw, str) else default


def _extract_weather_events_from_prior(
    prior_outputs: object,
) -> list[dict[str, object]]:
    """Extract the events list from the upstream ``weather_event_generator`` slot.

    Defence-in-depth: every malformed layer (non-dict prior, missing
    slot, no ``.files`` attr, non-dict ``.files``, missing
    ``assets/data/weather_events.json`` file, non-dict file, missing
    ``"events"`` key, non-list ``events`` value, non-dict entries)
    collapses silently to ``[]``.

    Does NOT sanitise per-event fields (intentional — sanitisation is
    generator-specific downstream).
    """
    gen = _get_prior_generator(prior_outputs, "weather_event_generator")
    files = getattr(gen, "files", None)
    if not isinstance(files, dict):
        return []
    payload = files.get("assets/data/weather_events.json")
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict)]


def _extract_weather_dialogue_from_prior(
    prior_outputs: object,
) -> dict[str, str]:
    """Extract the weather dialogue map from the upstream slot.

    Returns a flat ``{key: text}`` dict (the same shape the
    ``weather_npc_dialogue_generator`` writes into
    ``assets/data/weather_dialogue.json``). Defensive at every layer:
    non-dict prior, missing slot, no ``.files`` attr, non-dict
    ``.files``, missing dialogue file, non-dict file, or any
    non-string entry value all collapse to ``{}``. Non-string keys
    are coerced to ``str``; non-string values are silently dropped.
    """
    gen = _get_prior_generator(prior_outputs, "weather_npc_dialogue_generator")
    files = getattr(gen, "files", None)
    if not isinstance(files, dict):
        return {}
    payload = files.get("assets/data/weather_dialogue.json")
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            result[str(key)] = value
    return result


def _extract_weather_buffs_from_prior(
    prior_outputs: object,
) -> list[dict[str, object]]:
    """Extract the buffs list from the upstream ``weather_buff_generator`` slot.

    Same defensive contract as
    :func:`_extract_weather_events_from_prior` — every malformed layer
    collapses to ``[]`` rather than raising. Does NOT sanitise
    per-buff fields.
    """
    gen = _get_prior_generator(prior_outputs, "weather_buff_generator")
    files = getattr(gen, "files", None)
    if not isinstance(files, dict):
        return []
    payload = files.get("assets/data/weather_buffs.json")
    if not isinstance(payload, dict):
        return []
    buffs = payload.get("buffs")
    if not isinstance(buffs, list):
        return []
    return [b for b in buffs if isinstance(b, dict)]


def _extract_weather_mails_from_prior(
    prior_outputs: object,
) -> list[tuple[str, str]]:
    """Extract mail (key, text) tuples from the upstream ``weather_mail_generator`` slot.

    The mail generator writes one file per letter under ``mail/...``,
    where each file is a ``{mail_key: body}`` dict. This helper walks
    every ``mail/`` file, flattens it into ``(mail_key, body)`` pairs,
    and returns the list. Defensive at every layer: non-dict prior,
    missing slot, no ``.files`` attr, non-dict ``.files``, non-dict
    file, or non-string values all collapse to ``[]``.
    """
    gen = _get_prior_generator(prior_outputs, "weather_mail_generator")
    files = getattr(gen, "files", None)
    if not isinstance(files, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for path, payload in files.items():
        if not isinstance(path, str) or not path.startswith("mail/"):
            continue
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if isinstance(value, str):
                pairs.append((str(key), value))
    return pairs


class WeatherEventGenerator(BaseGenerator):
    name = "weather_event_generator"
    phase = "weather_event"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create weather-based events for Stardew Valley based on: "{inp["prompt"]}"

Generate 3-5 unique weather events. For each event provide:
- EventName: snake_case identifier
- WeatherCondition: one of "sunny", "rainy", "snowy", "windy", "stormy"
- Season: optional season filter ("spring", "summer", "fall", "winter") or null for all seasons
- Description: 1 sentence describing what happens
- Effects: list of {{"Stat": "Farming", "Value": 1, "Duration": 300}}

Use only valid SDV stats: "Farming", "Fishing", "Mining", "Foraging", "Luck", "Energy", "Health".
Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, WeatherEventOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            events = WeatherEventOutput(**result).events
            event_dicts = []
            for ev in events:
                event_dicts.append({
                    "EventName": ev.event_name,
                    "WeatherCondition": ev.weather_condition,
                    "Season": ev.season,
                    "Description": ev.description,
                    "Effects": ev.effects,
                })
            out.add_file("assets/data/weather_events.json", {"events": event_dicts})
            out.metadata["event_count"] = len(event_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("weather_event_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("assets/data/weather_events.json", {
                "events": [
                    {
                        "EventName": "Rainy_Day_Bonus",
                        "WeatherCondition": "rainy",
                        "Season": None,
                        "Description": "Crops grow 10% faster on rainy days.",
                        "Effects": [{"Stat": "Farming", "Value": 1, "Duration": 300}],
                    },
                    {
                        "EventName": "Stormy_Mining",
                        "WeatherCondition": "stormy",
                        "Season": None,
                        "Description": "Lightning energizes the mines, boosting mining speed.",
                        "Effects": [{"Stat": "Mining", "Value": 2, "Duration": 300}],
                    },
                    {
                        "EventName": "Snowy_Foraging",
                        "WeatherCondition": "snowy",
                        "Season": "winter",
                        "Description": "Winter berries are easier to spot in the snow.",
                        "Effects": [{"Stat": "Foraging", "Value": 1, "Duration": 300}],
                    },
                ]
            })
            out.metadata["event_count"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/weather_events.json"):
            errors.append("weather_event_generator: assets/data/weather_events.json missing")
        return errors


class WeatherNPCDialogueGenerator(BaseGenerator):
    name = "weather_npc_dialogue_generator"
    phase = "weather_event"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create weather-dependent NPC dialogue for Stardew Valley based on: "{inp["prompt"]}"

Generate 4-6 dialogue lines for different NPCs reacting to weather.
For each line provide:
- NPCName: valid SDV NPC name (e.g. "Abigail", "Sebastian", "Penny")
- WeatherCondition: one of "sunny", "rainy", "snowy", "windy", "stormy"
- Dialogue: 1-2 short sentences the NPC says when this weather occurs

Use @ for player name. Keep lines under 120 characters.
Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, WeatherNPCDialogueOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            dialogue = WeatherNPCDialogueOutput(**result)
            dialogue_dict: dict[str, str] = {}
            for entry in dialogue.dialogues:
                key = f"{entry.weather_condition}_{entry.npc_name.lower()}"
                dialogue_dict[key] = entry.dialogue
            out.add_file("assets/data/weather_dialogue.json", dialogue_dict)
            out.metadata["dialogue_count"] = len(dialogue.dialogues)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("weather_npc_dialogue_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("assets/data/weather_dialogue.json", {
                "rainy_abigail": "I love the rain, @. It makes everything feel so peaceful.",
                "sunny_sebastian": "Ugh, too bright. I prefer staying inside on sunny days.",
                "snowy_penny": "The snow is beautiful, isn't it @? Let's build a snowman!",
                "stormy_maru": "The lightning is fascinating! Did you know a bolt can reach 30,000 degrees?",
            })
            out.metadata["dialogue_count"] = 4
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/weather_dialogue.json"):
            errors.append("weather_npc_dialogue_generator: assets/data/weather_dialogue.json missing")
        return errors


class WeatherBuffGenerator(BaseGenerator):
    name = "weather_buff_generator"
    phase = "weather_event"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create weather-based buffs for Stardew Valley based on: "{inp["prompt"]}"

Generate 3-5 buffs that apply during specific weather. For each buff:
- BuffName: snake_case identifier
- WeatherCondition: one of "sunny", "rainy", "snowy", "windy", "stormy"
- Stat: one of "Farming", "Fishing", "Mining", "Foraging", "Luck", "Energy", "Health"
- Value: buff amount (1-5)
- Duration: seconds (default 300 = 5 minutes)

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, WeatherBuffOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            buffs = WeatherBuffOutput(**result).buffs
            buff_dicts = []
            for b in buffs:
                buff_dicts.append({
                    "BuffName": b.buff_name,
                    "WeatherCondition": b.weather_condition,
                    "Stat": b.stat,
                    "Value": b.value,
                    "Duration": b.duration,
                })
            out.add_file("assets/data/weather_buffs.json", {"buffs": buff_dicts})
            out.metadata["buff_count"] = len(buff_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("weather_buff_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("assets/data/weather_buffs.json", {
                "buffs": [
                    {"BuffName": "Sunny_Farming", "WeatherCondition": "sunny", "Stat": "Farming", "Value": 1, "Duration": 300},
                    {"BuffName": "Rainy_Fishing", "WeatherCondition": "rainy", "Stat": "Fishing", "Value": 2, "Duration": 300},
                    {"BuffName": "Stormy_Luck", "WeatherCondition": "stormy", "Stat": "Luck", "Value": 1, "Duration": 300},
                ]
            })
            out.metadata["buff_count"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/weather_buffs.json"):
            errors.append("weather_buff_generator: assets/data/weather_buffs.json missing")
        return errors


class WeatherMailGenerator(BaseGenerator):
    name = "weather_mail_generator"
    phase = "weather_event"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create weather announcement mails for Stardew Valley based on: "{inp["prompt"]}"

Generate 2-3 short mail letters about upcoming weather events.
For each mail provide:
- MailKey: snake_case identifier
- Subject: short subject line
- Body: 2-3 sentences about the weather event
- WeatherCondition: the weather this mail relates to

Use @ for player name. Keep under 200 characters.
Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, WeatherMailOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            mails = WeatherMailOutput(**result).mails
            for mail in mails:
                safe_key = _sanitize_weather_name(mail.mail_key)
                out.add_file(f"mail/{safe_key}.json", {safe_key: mail.body})
            out.metadata["mail_count"] = len(mails)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("weather_mail_generator.failed", error=str(exc), error_type=type(exc).__name__)
            out.add_file("mail/weather_announcement.json", {
                "weather_announcement": "Dear @, ^A big storm is forecast for tomorrow! ^Stay safe and enjoy the bonus mining energy. ^  - Gunther"
            })
            out.metadata["mail_count"] = 1
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not any(k.startswith("mail/") for k in output.files):
            errors.append("weather_mail_generator: no mail file generated")
        return errors


class WeatherContentJsonGenerator(BaseGenerator):
    name = "weather_content_json_generator"
    phase = "weather_event"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        # v44 — replace the 5 inline ``prior.get(...)`` chains with
        # shared helpers. Behaviour preserved (manifest → lowercase
        # mod_id, events → Data/WeatherEvents EditData blocks,
        # dialogue → Data/Characters/Dialogue/<NPC> EditData blocks,
        # buffs → Data/Buffs EditData blocks, mails → Data/mail
        # EditData blocks), but malformed layers now collapse silently
        # via the helpers instead of relying on inline
        # ``GeneratorOutput()`` defaults + ad-hoc isinstance checks.
        mod_id = _extract_mod_id_from_manifest_prior(prior)
        events = _extract_weather_events_from_prior(prior)
        dialogue_map = _extract_weather_dialogue_from_prior(prior)
        buffs = _extract_weather_buffs_from_prior(prior)
        mail_pairs = _extract_weather_mails_from_prior(prior)

        changes: list[dict] = []

        # Add weather events
        for ev in events:
            ev_name = ev.get("EventName", "unknown")
            changes.append({
                "Action": "EditData",
                "Target": "Data/WeatherEvents",
                "Entries": {
                    ev_name: {
                        "WeatherCondition": ev.get("WeatherCondition", ""),
                        "Season": ev.get("Season"),
                        "Description": ev.get("Description", ""),
                        "Effects": ev.get("Effects", []),
                    }
                },
            })

        # Add weather dialogue
        for key, text in dialogue_map.items():
            parts = key.split("_", 1)
            if len(parts) == 2:
                weather, npc = parts
                changes.append({
                    "Action": "EditData",
                    "Target": f"Data/Characters/Dialogue/{npc.capitalize()}",
                    "Entries": {
                        f"Weather_{weather.capitalize()}": text,
                    },
                    "When": {"Weather": weather},
                })

        # Add weather buffs
        for b in buffs:
            b_name = b.get("BuffName", "unknown")
            changes.append({
                "Action": "EditData",
                "Target": "Data/Buffs",
                "Entries": {
                    b_name: {
                        "Stat": b.get("Stat", ""),
                        "Value": b.get("Value", 0),
                        "Duration": b.get("Duration", 300),
                        "WeatherCondition": b.get("WeatherCondition", ""),
                    }
                },
            })

        # Add mail entries
        for letter_key, letter_text in mail_pairs:
            changes.append({
                "Action": "EditData",
                "Target": "Data/mail",
                "Entries": {
                    letter_key: {
                        "text": letter_text,
                        "broadcast": True,
                    }
                },
            })

        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": changes,
        })
        out.metadata["mod_id"] = mod_id
        out.metadata["changes_count"] = len(changes)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("weather_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("weather_content_json_generator: content.json must be a dict")
        elif "Changes" not in content:
            errors.append("weather_content_json_generator: Changes key missing")
        return errors
