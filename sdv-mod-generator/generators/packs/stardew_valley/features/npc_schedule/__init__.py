"""NPC schedule feature generators for Stardew Valley."""
from pydantic import BaseModel, Field, ValidationError

import structlog
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger()


class NPCScheduleEntry(BaseModel):
    time: str = Field(validation_alias="Time")
    location: str = Field(validation_alias="Location")
    x: int = Field(default=0, validation_alias="X")
    y: int = Field(default=0, validation_alias="Y")
    action: str | None = Field(default=None, validation_alias="Action")


class NPCScheduleOutput(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    schedule_entries: list[NPCScheduleEntry] = Field(validation_alias="ScheduleEntries")


class NPCDialogueOutput(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    dialogues: list[dict[str, str]] = Field(validation_alias="Dialogues")


class NPCGiftTasteOutput(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    loves: list[str] = Field(default_factory=list, validation_alias="Loves")
    likes: list[str] = Field(default_factory=list, validation_alias="Likes")
    neutral: list[str] = Field(default_factory=list, validation_alias="Neutral")
    dislikes: list[str] = Field(default_factory=list, validation_alias="Dislikes")
    hates: list[str] = Field(default_factory=list, validation_alias="Hates")


def _sanitize_npc_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c == "_") or "UnknownNPC"


class NPCScheduleGenerator(BaseGenerator):
    name = "npc_schedule_generator"
    phase = "npc_schedule"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create an NPC schedule for Stardew Valley based on: "{inp["prompt"]}"

Generate a daily routine with 4-8 time slots.
For each entry provide:
- Time: in 24h format (e.g. "0800", "1200", "1800")
- Location: valid SDV location (e.g. "Town", "Farm", "Beach", "Mountain")
- X, Y: coordinates (optional, default 0)
- Action: optional action string (e.g. "sit", "read", "fish")

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, NPCScheduleOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            sched = NPCScheduleOutput(**result)
            npc = _sanitize_npc_name(sched.npc_name)
            schedule_dict: dict[str, str | dict] = {"name": npc}
            for entry in sched.schedule_entries:
                time_key = entry.time
                schedule_dict[time_key] = f"{entry.location} {entry.x} {entry.y}"
                if entry.action:
                    schedule_dict[f"{time_key}_action"] = entry.action
            out.add_file(f"assets/schedules/{npc}.json", schedule_dict)
            out.metadata["npc_name"] = npc
            out.metadata["schedule_entries"] = len(sched.schedule_entries)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("npc_schedule_generator.failed", error=str(exc))
            out.add_file("assets/schedules/UnknownNPC.json", {
                "name": "UnknownNPC",
                "0800": "Farm 64 15",
                "1200": "Town 44 68",
                "1800": "FarmHouse 3 5",
            })
            out.metadata["npc_name"] = "UnknownNPC"
            out.metadata["schedule_entries"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        schedule_files = [k for k in output.files if k.startswith("assets/schedules/")]
        if not schedule_files:
            errors.append("npc_schedule_generator: no schedule file generated")
        return errors


class NPCDialogueGenerator(BaseGenerator):
    name = "npc_dialogue_generator"
    phase = "npc_schedule"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        npc_name = prior.get("npc_schedule_generator", GeneratorOutput()).metadata.get("npc_name", "UnknownNPC")

        prompt = f'''Create dialogue lines for NPC "{npc_name}" in Stardew Valley based on: "{inp["prompt"]}"

Generate 3-5 dialogue entries with context keys like "Mon", "Tue", "spring", "rain".
Each entry: {{"key": "<context>", "text": "<dialogue line>"}}
Keep lines under 120 characters. Use @ for player name.'''

        try:
            result = await generate_structured(
                prompt, NPCDialogueOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            dialogue = NPCDialogueOutput(**result)
            npc = _sanitize_npc_name(dialogue.npc_name or npc_name)
            dialogue_dict: dict[str, str] = {}
            for entry in dialogue.dialogues:
                if isinstance(entry, dict):
                    key = entry.get("key", "default")
                    text = entry.get("text", "...")
                    dialogue_dict[key] = text
            out.add_file(f"assets/dialogue/{npc}.json", dialogue_dict)
            out.metadata["npc_name"] = npc
            out.metadata["dialogue_count"] = len(dialogue.dialogues)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("npc_dialogue_generator.failed", error=str(exc))
            npc = _sanitize_npc_name(npc_name)
            out.add_file(f"assets/dialogue/{npc}.json", {
                "Mon": f"Hi @, it's a new week!",
                "Tue": "The farm looks great today.",
                "rain": "I don't like getting wet...",
            })
            out.metadata["npc_name"] = npc
            out.metadata["dialogue_count"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        dialogue_files = [k for k in output.files if k.startswith("assets/dialogue/")]
        if not dialogue_files:
            errors.append("npc_dialogue_generator: no dialogue file generated")
        return errors


class NPCGiftTasteGenerator(BaseGenerator):
    name = "npc_gift_taste_generator"
    phase = "npc_schedule"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        npc_name = prior.get("npc_schedule_generator", GeneratorOutput()).metadata.get("npc_name", "UnknownNPC")

        prompt = f'''Generate gift taste preferences for NPC "{npc_name}" in Stardew Valley based on: "{inp["prompt"]}"

Provide lists of item names for each category:
- Loves: 2-4 items
- Likes: 3-5 items
- Neutral: 2-3 items
- Dislikes: 2-3 items
- Hates: 1-2 items'''

        try:
            result = await generate_structured(
                prompt, NPCGiftTasteOutput,
                system=llm_system_prompt(),
                max_tokens=2048,
            )
            tastes = NPCGiftTasteOutput(**result)
            npc = _sanitize_npc_name(tastes.npc_name or npc_name)
            out.add_file(f"assets/gift_tastes/{npc}.json", {
                "NPCName": npc,
                "Loves": tastes.loves,
                "Likes": tastes.likes,
                "Neutral": tastes.neutral,
                "Dislikes": tastes.dislikes,
                "Hates": tastes.hates,
            })
            out.metadata["npc_name"] = npc
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("npc_gift_taste_generator.failed", error=str(exc))
            npc = _sanitize_npc_name(npc_name)
            out.add_file(f"assets/gift_tastes/{npc}.json", {
                "NPCName": npc,
                "Loves": ["Diamond", "Prismatic Shard"],
                "Likes": ["Daffodil", "Salmonberry"],
                "Neutral": ["Milk", "Egg"],
                "Dislikes": ["Holly", "Stone"],
                "Hates": ["Coal"],
            })
            out.metadata["npc_name"] = npc
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        taste_files = [k for k in output.files if k.startswith("assets/gift_tastes/")]
        if not taste_files:
            errors.append("npc_gift_taste_generator: no gift taste file generated")
        return errors


class NPCContentJsonGenerator(BaseGenerator):
    name = "npc_content_json_generator"
    phase = "npc_schedule"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        mod_id = manifest_data.get("UniqueID", "NPCScheduleMod").lower()

        schedule_gen = prior.get("npc_schedule_generator", GeneratorOutput())
        npc_name = schedule_gen.metadata.get("npc_name", "UnknownNPC")
        schedule_file = f"assets/schedules/{npc_name}.json"

        dialogue_gen = prior.get("npc_dialogue_generator", GeneratorOutput())
        dialogue_file = f"assets/dialogue/{npc_name}.json"

        taste_gen = prior.get("npc_gift_taste_generator", GeneratorOutput())
        taste_file = f"assets/gift_tastes/{npc_name}.json"

        changes: list[dict] = []

        if schedule_file in schedule_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Characters/Schedules/{npc_name}",
                "FromFile": schedule_file,
            })

        if dialogue_file in dialogue_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Characters/Dialogue/{npc_name}",
                "FromFile": dialogue_file,
            })

        if taste_file in taste_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Characters/Dialogue/{npc_name}",
                "Entries": {
                    "spring_1": f"Hi @, I'm {npc_name}. Nice to meet you!",
                },
                "When": {"HasMod": f"{mod_id}"},
            })

        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": changes,
        })
        out.metadata["mod_id"] = mod_id
        out.metadata["npc_name"] = npc_name
        out.metadata["changes_count"] = len(changes)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("npc_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("npc_content_json_generator: content.json must be a dict")
        elif "Changes" not in content:
            errors.append("npc_content_json_generator: Changes key missing")
        return errors
