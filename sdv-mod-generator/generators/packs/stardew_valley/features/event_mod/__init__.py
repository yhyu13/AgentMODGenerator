"""Festival event feature generators for Stardew Valley.

Generates custom festival events, including:
- Festival manifest and schedule
- Festival map data and NPC positions
- Festival shop items and minigames
- Festival dialogue and mail announcements
"""
from pydantic import BaseModel, Field, ValidationError

import structlog
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger()


class FestivalScheduleEntry(BaseModel):
    time: str = Field(validation_alias="Time")
    event_name: str = Field(validation_alias="EventName")
    location: str = Field(validation_alias="Location")
    description: str = Field(default="", validation_alias="Description")


class FestivalScheduleOutput(BaseModel):
    festival_name: str = Field(validation_alias="FestivalName")
    season: str = Field(validation_alias="Season")
    day: int = Field(validation_alias="Day")
    schedule_entries: list[FestivalScheduleEntry] = Field(validation_alias="ScheduleEntries")


class FestivalShopItem(BaseModel):
    item_name: str = Field(validation_alias="ItemName")
    price: int = Field(validation_alias="Price")
    stock: int = Field(default=5, validation_alias="Stock")


class FestivalShopOutput(BaseModel):
    shop_name: str = Field(validation_alias="ShopName")
    items: list[FestivalShopItem] = Field(validation_alias="Items")


class FestivalNPCPosition(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    x: int = Field(validation_alias="X")
    y: int = Field(validation_alias="Y")
    direction: str = Field(default="down", validation_alias="Direction")


class FestivalMapOutput(BaseModel):
    map_name: str = Field(validation_alias="MapName")
    npc_positions: list[FestivalNPCPosition] = Field(validation_alias="NPCPositions")


class FestivalDialogueEntry(BaseModel):
    npc_name: str = Field(validation_alias="NPCName")
    dialogue: str = Field(validation_alias="Dialogue")
    context: str = Field(default="festival", validation_alias="Context")


class FestivalDialogueOutput(BaseModel):
    dialogues: list[FestivalDialogueEntry] = Field(validation_alias="Dialogues")


class FestivalMailOutput(BaseModel):
    subject: str = Field(validation_alias="Subject")
    body: str = Field(validation_alias="Body")


def _sanitize_festival_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c == "_") or "CustomFestival"


class FestivalScheduleGenerator(BaseGenerator):
    name = "festival_schedule_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create a Stardew Valley festival event based on: "{inp["prompt"]}"

Generate a festival schedule with:
- FestivalName: a creative name for the festival
- Season: one of "spring", "summer", "fall", "winter"
- Day: day of the month (1-28)
- ScheduleEntries: 3-5 time-based events during the festival

For each entry provide:
- Time: in 24h format (e.g. "0900", "1200", "1800")
- EventName: name of the activity
- Location: valid SDV location (e.g. "Town", "Beach", "Mountain", "Forest")
- Description: 1 sentence describing the activity

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, FestivalScheduleOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            sched = FestivalScheduleOutput(**result)
            festival = _sanitize_festival_name(sched.festival_name)
            schedule_dict: dict[str, str | dict] = {
                "name": festival,
                "season": sched.season,
                "day": sched.day,
            }
            for entry in sched.schedule_entries:
                schedule_dict[entry.time] = {
                    "event_name": entry.event_name,
                    "location": entry.location,
                    "description": entry.description,
                }
            out.add_file(f"assets/festivals/{festival}_schedule.json", schedule_dict)
            out.metadata["festival_name"] = festival
            out.metadata["season"] = sched.season
            out.metadata["day"] = sched.day
            out.metadata["schedule_entries"] = len(sched.schedule_entries)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("festival_schedule_generator.failed", error=str(exc))
            out.add_file("assets/festivals/CustomFestival_schedule.json", {
                "name": "CustomFestival",
                "season": "spring",
                "day": 13,
                "0900": {"event_name": "Opening Ceremony", "location": "Town", "description": "The festival begins!"},
                "1200": {"event_name": "Main Event", "location": "Town", "description": "The main festival activity."},
                "1800": {"event_name": "Closing Ceremony", "location": "Town", "description": "The festival ends."},
            })
            out.metadata["festival_name"] = "CustomFestival"
            out.metadata["season"] = "spring"
            out.metadata["day"] = 13
            out.metadata["schedule_entries"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        schedule_files = [k for k in output.files if k.startswith("assets/festivals/") and k.endswith("_schedule.json")]
        if not schedule_files:
            errors.append("festival_schedule_generator: no schedule file generated")
        return errors


class FestivalShopGenerator(BaseGenerator):
    name = "festival_shop_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        festival_name = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("festival_name", "CustomFestival")

        prompt = f'''Create a festival shop for "{festival_name}" festival in Stardew Valley based on: "{inp["prompt"]}"

Generate 4-8 special festival items with:
- ItemName: creative festival-themed item name
- Price: gold (50-2000g)
- Stock: quantity available (1-20)

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, FestivalShopOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            shop = FestivalShopOutput(**result)
            items = []
            for item in shop.items:
                items.append({
                    "ItemName": item.item_name,
                    "Price": item.price,
                    "Stock": item.stock,
                })
            out.add_file(f"assets/festivals/{festival_name}_shop.json", {
                "shop_name": shop.shop_name,
                "items": items,
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["shop_items"] = len(items)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("festival_shop_generator.failed", error=str(exc))
            out.add_file(f"assets/festivals/{festival_name}_shop.json", {
                "shop_name": f"{festival_name} Shop",
                "items": [
                    {"ItemName": "Festival Token", "Price": 100, "Stock": 10},
                    {"ItemName": "Special Cake", "Price": 250, "Stock": 5},
                ],
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["shop_items"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        shop_files = [k for k in output.files if k.startswith("assets/festivals/") and k.endswith("_shop.json")]
        if not shop_files:
            errors.append("festival_shop_generator: no shop file generated")
        return errors


class FestivalMapGenerator(BaseGenerator):
    name = "festival_map_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        festival_name = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("festival_name", "CustomFestival")

        prompt = f'''Create festival map NPC positions for "{festival_name}" festival in Stardew Valley.

Place 5-10 NPCs around the festival area with:
- NPCName: valid SDV NPC name (e.g. "Abigail", "Sebastian", "Penny")
- X, Y: coordinates (0-100)
- Direction: "up", "down", "left", "right"

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, FestivalMapOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            map_data = FestivalMapOutput(**result)
            positions = []
            for pos in map_data.npc_positions:
                positions.append({
                    "NPCName": pos.npc_name,
                    "X": pos.x,
                    "Y": pos.y,
                    "Direction": pos.direction,
                })
            out.add_file(f"assets/festivals/{festival_name}_map.json", {
                "map_name": map_data.map_name,
                "npc_positions": positions,
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["npc_count"] = len(positions)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("festival_map_generator.failed", error=str(exc))
            out.add_file(f"assets/festivals/{festival_name}_map.json", {
                "map_name": "Town",
                "npc_positions": [
                    {"NPCName": "Abigail", "X": 30, "Y": 40, "Direction": "down"},
                    {"NPCName": "Sebastian", "X": 50, "Y": 30, "Direction": "left"},
                    {"NPCName": "Penny", "X": 70, "Y": 50, "Direction": "up"},
                ],
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["npc_count"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        map_files = [k for k in output.files if k.startswith("assets/festivals/") and k.endswith("_map.json")]
        if not map_files:
            errors.append("festival_map_generator: no map file generated")
        return errors


class FestivalDialogueGenerator(BaseGenerator):
    name = "festival_dialogue_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        festival_name = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("festival_name", "CustomFestival")

        prompt = f'''Create festival dialogue lines for "{festival_name}" festival in Stardew Valley based on: "{inp["prompt"]}"

Generate 3-6 dialogue lines for different NPCs:
- NPCName: valid SDV NPC name
- Dialogue: 1-2 short sentences the NPC says during the festival
- Context: "festival", "shop", "minigame", or "closing"

Use @ for player name. Keep lines under 120 characters.

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, FestivalDialogueOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            dialogue = FestivalDialogueOutput(**result)
            dialogue_dict: dict[str, str] = {}
            for entry in dialogue.dialogues:
                key = f"{entry.context}_{entry.npc_name.lower()}"
                dialogue_dict[key] = entry.dialogue
            out.add_file(f"assets/festivals/{festival_name}_dialogue.json", dialogue_dict)
            out.metadata["festival_name"] = festival_name
            out.metadata["dialogue_count"] = len(dialogue.dialogues)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("festival_dialogue_generator.failed", error=str(exc))
            npc = "Abigail"
            out.add_file(f"assets/festivals/{festival_name}_dialogue.json", {
                "festival_abigail": f"Hi @! Welcome to the {festival_name}!",
                "festival_sebastian": "This festival is pretty cool, I guess.",
                "closing_penny": "I hope you enjoyed the festival, @!",
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["dialogue_count"] = 3
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        dialogue_files = [k for k in output.files if k.startswith("assets/festivals/") and k.endswith("_dialogue.json")]
        if not dialogue_files:
            errors.append("festival_dialogue_generator: no dialogue file generated")
        return errors


class FestivalMailGenerator(BaseGenerator):
    name = "festival_mail_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})
        festival_name = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("festival_name", "CustomFestival")
        season = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("season", "spring")
        day = prior.get("festival_schedule_generator", GeneratorOutput()).metadata.get("day", 13)

        prompt = f'''Create a festival announcement mail for "{festival_name}" in Stardew Valley.

The festival is on {season} {day}. Write a short, exciting announcement mail:
- Subject: short subject line
- Body: 2-3 sentences inviting the player to the festival

Use @ for player name. Keep it under 200 characters.

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, FestivalMailOutput,
                system=llm_system_prompt(),
                max_tokens=2048,
            )
            mail = FestivalMailOutput(**result)
            mail_key = f"{festival_name.lower()}_announcement"
            out.add_file(f"mail/{mail_key}.json", {mail_key: mail.body})
            out.metadata["festival_name"] = festival_name
            out.metadata["mail_key"] = mail_key
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("festival_mail_generator.failed", error=str(exc))
            mail_key = f"{festival_name.lower()}_announcement"
            out.add_file(f"mail/{mail_key}.json", {
                mail_key: f"Dear @, ^The {festival_name} is coming up on {season} {day}! ^Come join us for fun activities and special items!^  - Mayor Lewis"
            })
            out.metadata["festival_name"] = festival_name
            out.metadata["mail_key"] = mail_key
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        mail_files = [k for k in output.files if k.startswith("mail/") and "announcement" in k]
        if not mail_files:
            errors.append("festival_mail_generator: no announcement mail file generated")
        return errors


class FestivalContentJsonGenerator(BaseGenerator):
    name = "festival_content_json_generator"
    phase = "event_mod"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        mod_id = manifest_data.get("UniqueID", "FestivalMod").lower()

        schedule_gen = prior.get("festival_schedule_generator", GeneratorOutput())
        festival_name = schedule_gen.metadata.get("festival_name", "CustomFestival")
        season = schedule_gen.metadata.get("season", "spring")
        day = schedule_gen.metadata.get("day", 13)
        schedule_file = f"assets/festivals/{festival_name}_schedule.json"

        shop_gen = prior.get("festival_shop_generator", GeneratorOutput())
        shop_file = f"assets/festivals/{festival_name}_shop.json"

        map_gen = prior.get("festival_map_generator", GeneratorOutput())
        map_file = f"assets/festivals/{festival_name}_map.json"

        dialogue_gen = prior.get("festival_dialogue_generator", GeneratorOutput())
        dialogue_file = f"assets/festivals/{festival_name}_dialogue.json"

        mail_gen = prior.get("festival_mail_generator", GeneratorOutput())
        mail_key = mail_gen.metadata.get("mail_key", f"{festival_name.lower()}_announcement")

        changes: list[dict] = []

        # Add festival schedule data
        if schedule_file in schedule_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Data/Festivals/{festival_name}",
                "FromFile": schedule_file,
                "When": {"Season": season, "Day": day},
            })

        # Add festival shop
        if shop_file in shop_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Data/Shops/{festival_name}_Shop",
                "FromFile": shop_file,
                "When": {"Season": season, "Day": day},
            })

        # Add festival map NPC positions
        if map_file in map_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Data/Festivals/{festival_name}_NPCs",
                "FromFile": map_file,
                "When": {"Season": season, "Day": day},
            })

        # Add festival dialogue
        if dialogue_file in dialogue_gen.files:
            changes.append({
                "Action": "EditData",
                "Target": f"Data/Festivals/{festival_name}_Dialogue",
                "FromFile": dialogue_file,
                "When": {"Season": season, "Day": day},
            })

        # Add mail announcement
        mail_file_name = f"mail/{mail_key}.json"
        if mail_file_name in mail_gen.files:
            mail_content = mail_gen.files[mail_file_name]
            if isinstance(mail_content, dict):
                for letter_key, letter_text in mail_content.items():
                    changes.append({
                        "Action": "EditData",
                        "Target": "Data/mail",
                        "Entries": {
                            letter_key: {
                                "text": letter_text,
                                "broadcast": True,
                            }
                        },
                        "When": {"Season": season, "Day": max(1, day - 2)},
                    })

        out.add_file("content.json", {
            "Format": "1.29.0",
            "Changes": changes,
        })
        out.metadata["mod_id"] = mod_id
        out.metadata["festival_name"] = festival_name
        out.metadata["changes_count"] = len(changes)
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        content = output.files.get("content.json")
        if not content:
            errors.append("festival_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("festival_content_json_generator: content.json must be a dict")
        elif "Changes" not in content:
            errors.append("festival_content_json_generator: Changes key missing")
        return errors
