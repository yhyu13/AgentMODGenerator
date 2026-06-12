"""Farm expansion feature generators for Stardew Valley.

Generates custom farm buildings, map edits, and warp points for farm expansion mods.
"""
from pydantic import BaseModel, Field, ValidationError

import structlog
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
from generators.llm_utils import generate_structured, llm_system_prompt

logger = structlog.get_logger()


class BuildingEntry(BaseModel):
    building_name: str = Field(validation_alias="BuildingName")
    building_type: str = Field(validation_alias="BuildingType")
    width: int = Field(default=3, validation_alias="Width")
    height: int = Field(default=3, validation_alias="Height")
    description: str = Field(default="", validation_alias="Description")
    cost: int = Field(default=500, validation_alias="Cost")
    materials: list[dict[str, str | int]] = Field(default_factory=list, validation_alias="Materials")


class BuildingOutput(BaseModel):
    buildings: list[BuildingEntry] = Field(validation_alias="Buildings")


class WarpPoint(BaseModel):
    warp_name: str = Field(validation_alias="WarpName")
    from_map: str = Field(validation_alias="FromMap")
    from_x: int = Field(validation_alias="FromX")
    from_y: int = Field(validation_alias="FromY")
    to_map: str = Field(validation_alias="ToMap")
    to_x: int = Field(validation_alias="ToX")
    to_y: int = Field(validation_alias="ToY")


class WarpOutput(BaseModel):
    warps: list[WarpPoint] = Field(validation_alias="Warps")


class MapEditEntry(BaseModel):
    edit_type: str = Field(validation_alias="EditType")
    target_map: str = Field(validation_alias="TargetMap")
    layer: str = Field(default="Back", validation_alias="Layer")
    x: int = Field(validation_alias="X")
    y: int = Field(validation_alias="Y")
    tile_index: int | None = Field(default=None, validation_alias="TileIndex")
    tile_sheet: str | None = Field(default=None, validation_alias="TileSheet")


class MapEditOutput(BaseModel):
    edits: list[MapEditEntry] = Field(validation_alias="Edits")


def _sanitize_building_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c == "_") or "CustomBuilding"


class BuildingGenerator(BaseGenerator):
    name = "building_generator"
    phase = "farm_expansion"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create custom farm buildings for Stardew Valley based on: "{inp["prompt"]}"

Generate 2-4 unique buildings. For each building provide:
- BuildingName: snake_case identifier
- BuildingType: "Barn", "Coop", "Shed", "Greenhouse", "Silos", or "Custom"
- Width, Height: tile dimensions (2-8)
- Description: 1 sentence
- Cost: gold (100-5000g)
- Materials: list of {{"ItemName": "<sdv item>", "Quantity": <int>}}

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, BuildingOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            buildings = BuildingOutput(**result).buildings
            building_dicts = []
            for b in buildings:
                building_dicts.append({
                    "BuildingName": b.building_name,
                    "BuildingType": b.building_type,
                    "Width": b.width,
                    "Height": b.height,
                    "Description": b.description,
                    "Cost": b.cost,
                    "Materials": b.materials,
                })
            out.add_file("assets/data/buildings.json", {"buildings": building_dicts})
            out.metadata["building_count"] = len(building_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("building_generator.failed", error=str(exc))
            out.add_file("assets/data/buildings.json", {
                "buildings": [
                    {
                        "BuildingName": "Custom_Shed",
                        "BuildingType": "Shed",
                        "Width": 3,
                        "Height": 3,
                        "Description": "A small shed for extra storage.",
                        "Cost": 300,
                        "Materials": [{"ItemName": "Wood", "Quantity": 100}],
                    },
                    {
                        "BuildingName": "Greenhouse_Expansion",
                        "BuildingType": "Greenhouse",
                        "Width": 5,
                        "Height": 4,
                        "Description": "An expanded greenhouse for more crops.",
                        "Cost": 2000,
                        "Materials": [{"ItemName": "Wood", "Quantity": 300}, {"ItemName": "Stone", "Quantity": 200}],
                    },
                ]
            })
            out.metadata["building_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/buildings.json"):
            errors.append("building_generator: assets/data/buildings.json missing")
        return errors


class WarpPointGenerator(BaseGenerator):
    name = "warp_point_generator"
    phase = "farm_expansion"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create warp points for a farm expansion mod in Stardew Valley based on: "{inp["prompt"]}"

Generate 2-4 warp points connecting new areas to existing maps.
For each warp provide:
- WarpName: snake_case identifier
- FromMap: source map (e.g. "Farm", "Town", "Beach")
- FromX, FromY: source coordinates (0-100)
- ToMap: destination map
- ToX, ToY: destination coordinates

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, WarpOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            warps = WarpOutput(**result).warps
            warp_dicts = []
            for w in warps:
                warp_dicts.append({
                    "WarpName": w.warp_name,
                    "FromMap": w.from_map,
                    "FromX": w.from_x,
                    "FromY": w.from_y,
                    "ToMap": w.to_map,
                    "ToX": w.to_x,
                    "ToY": w.to_y,
                })
            out.add_file("assets/data/warps.json", {"warps": warp_dicts})
            out.metadata["warp_count"] = len(warp_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("warp_point_generator.failed", error=str(exc))
            out.add_file("assets/data/warps.json", {
                "warps": [
                    {
                        "WarpName": "farm_to_expansion",
                        "FromMap": "Farm",
                        "FromX": 50,
                        "FromY": 10,
                        "ToMap": "FarmExpansion",
                        "ToX": 5,
                        "ToY": 50,
                    },
                    {
                        "WarpName": "expansion_to_farm",
                        "FromMap": "FarmExpansion",
                        "FromX": 5,
                        "FromY": 50,
                        "ToMap": "Farm",
                        "ToX": 50,
                        "ToY": 10,
                    },
                ]
            })
            out.metadata["warp_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/warps.json"):
            errors.append("warp_point_generator: assets/data/warps.json missing")
        return errors


class MapEditGenerator(BaseGenerator):
    name = "map_edit_generator"
    phase = "farm_expansion"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prompt = f'''Create map edits for a farm expansion mod in Stardew Valley based on: "{inp["prompt"]}"

Generate 3-6 tile edits for the farm or new areas.
For each edit provide:
- EditType: "AddTile" or "RemoveTile"
- TargetMap: map name (e.g. "Farm", "FarmExpansion")
- Layer: "Back", "Buildings", "Front", or "AlwaysFront"
- X, Y: tile coordinates
- TileIndex: optional tile sheet index
- TileSheet: optional tile sheet name

Respond with ONLY valid JSON matching the expected schema.'''

        try:
            result = await generate_structured(
                prompt, MapEditOutput,
                system=llm_system_prompt(),
                max_tokens=4096,
            )
            edits = MapEditOutput(**result).edits
            edit_dicts = []
            for e in edits:
                edit_dicts.append({
                    "EditType": e.edit_type,
                    "TargetMap": e.target_map,
                    "Layer": e.layer,
                    "X": e.x,
                    "Y": e.y,
                    "TileIndex": e.tile_index,
                    "TileSheet": e.tile_sheet,
                })
            out.add_file("assets/data/map_edits.json", {"edits": edit_dicts})
            out.metadata["edit_count"] = len(edit_dicts)
        except (ValueError, RuntimeError, IOError, ValidationError) as exc:
            logger.error("map_edit_generator.failed", error=str(exc))
            out.add_file("assets/data/map_edits.json", {
                "edits": [
                    {
                        "EditType": "AddTile",
                        "TargetMap": "Farm",
                        "Layer": "Back",
                        "X": 60,
                        "Y": 20,
                        "TileIndex": 0,
                        "TileSheet": "spring_outdoorsTileSheet",
                    },
                    {
                        "EditType": "AddTile",
                        "TargetMap": "Farm",
                        "Layer": "Buildings",
                        "X": 61,
                        "Y": 20,
                        "TileIndex": 1,
                        "TileSheet": "spring_outdoorsTileSheet",
                    },
                ]
            })
            out.metadata["edit_count"] = 2
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if not output.files.get("assets/data/map_edits.json"):
            errors.append("map_edit_generator: assets/data/map_edits.json missing")
        return errors


class FarmExpansionContentJsonGenerator(BaseGenerator):
    name = "farm_expansion_content_json_generator"
    phase = "farm_expansion"
    game = "stardew_valley"

    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput()
        prior = inp.get("prior_outputs", {})

        manifest_data = prior.get("manifest_generator", GeneratorOutput()).files.get("manifest.json", {})
        mod_id = manifest_data.get("UniqueID", "FarmExpansionMod").lower()

        building_gen = prior.get("building_generator", GeneratorOutput())
        warp_gen = prior.get("warp_point_generator", GeneratorOutput())
        map_gen = prior.get("map_edit_generator", GeneratorOutput())

        changes: list[dict] = []

        building_file = "assets/data/buildings.json"
        if building_file in building_gen.files:
            building_data = building_gen.files[building_file]
            if isinstance(building_data, dict) and isinstance(building_data.get("buildings"), list):
                for b in building_data["buildings"]:
                    b_name = b.get("BuildingName", "unknown")
                    changes.append({
                        "Action": "EditData",
                        "Target": "Data/Buildings",
                        "Entries": {
                            b_name: {
                                "BuildingType": b.get("BuildingType", "Custom"),
                                "Width": b.get("Width", 3),
                                "Height": b.get("Height", 3),
                                "Description": b.get("Description", ""),
                                "Cost": b.get("Cost", 500),
                                "Materials": b.get("Materials", []),
                            }
                        },
                    })

        warp_file = "assets/data/warps.json"
        if warp_file in warp_gen.files:
            warp_data = warp_gen.files[warp_file]
            if isinstance(warp_data, dict) and isinstance(warp_data.get("warps"), list):
                for w in warp_data["warps"]:
                    w_name = w.get("WarpName", "unknown")
                    changes.append({
                        "Action": "EditData",
                        "Target": "Data/Warps",
                        "Entries": {
                            w_name: {
                                "FromMap": w.get("FromMap", ""),
                                "FromX": w.get("FromX", 0),
                                "FromY": w.get("FromY", 0),
                                "ToMap": w.get("ToMap", ""),
                                "ToX": w.get("ToX", 0),
                                "ToY": w.get("ToY", 0),
                            }
                        },
                    })

        map_file = "assets/data/map_edits.json"
        if map_file in map_gen.files:
            map_data = map_gen.files[map_file]
            if isinstance(map_data, dict) and isinstance(map_data.get("edits"), list):
                for e in map_data["edits"]:
                    target_map = e.get("TargetMap", "Farm")
                    changes.append({
                        "Action": "EditMap",
                        "Target": f"Maps/{target_map}",
                        "MapTiles": [
                            {
                                "Layer": e.get("Layer", "Back"),
                                "X": e.get("X", 0),
                                "Y": e.get("Y", 0),
                                "TileIndex": e.get("TileIndex"),
                                "TileSheet": e.get("TileSheet"),
                            }
                        ],
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
            errors.append("farm_expansion_content_json_generator: content.json missing")
            return errors
        if not isinstance(content, dict):
            errors.append("farm_expansion_content_json_generator: content.json must be a dict")
        elif "Changes" not in content:
            errors.append("farm_expansion_content_json_generator: Changes key missing")
        return errors
