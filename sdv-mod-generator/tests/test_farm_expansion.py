"""Tests for the farm_expansion feature generators."""

import pytest
from generators.packs.stardew_valley.features.farm_expansion import (
    BuildingGenerator,
    WarpPointGenerator,
    MapEditGenerator,
    FarmExpansionContentJsonGenerator,
)
from generators.core.base import GeneratorInput, GeneratorOutput


class TestBuildingGenerator:
    """Tests for BuildingGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = BuildingGenerator()
        inp = GeneratorInput(prompt="Create a custom barn expansion")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "building_count" in out.metadata
        assert out.metadata["building_count"] >= 1

    @pytest.mark.asyncio
    async def test_buildings_file_generated(self):
        gen = BuildingGenerator()
        inp = GeneratorInput(prompt="Add new farm buildings")
        out = await gen.generate(inp)
        assert "assets/data/buildings.json" in out.files
        data = out.files["assets/data/buildings.json"]
        assert "buildings" in data
        assert len(data["buildings"]) >= 1
        assert "BuildingName" in data["buildings"][0]
        assert "BuildingType" in data["buildings"][0]

    def test_validate_output_detects_missing_file(self):
        gen = BuildingGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("assets/data/buildings.json missing" in e for e in errors)

    def test_validate_output_passes_with_file(self):
        gen = BuildingGenerator()
        out = GeneratorOutput()
        out.add_file("assets/data/buildings.json", {"buildings": []})
        errors = gen.validate_output(out)
        assert not errors


class TestWarpPointGenerator:
    """Tests for WarpPointGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = WarpPointGenerator()
        inp = GeneratorInput(prompt="Add warp points to new farm area")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "warp_count" in out.metadata
        assert out.metadata["warp_count"] >= 1

    @pytest.mark.asyncio
    async def test_warps_file_generated(self):
        gen = WarpPointGenerator()
        inp = GeneratorInput(prompt="Connect farm to beach")
        out = await gen.generate(inp)
        assert "assets/data/warps.json" in out.files
        data = out.files["assets/data/warps.json"]
        assert "warps" in data
        assert len(data["warps"]) >= 1
        assert "WarpName" in data["warps"][0]
        assert "FromMap" in data["warps"][0]
        assert "ToMap" in data["warps"][0]

    def test_validate_output_detects_missing_file(self):
        gen = WarpPointGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("assets/data/warps.json missing" in e for e in errors)


class TestMapEditGenerator:
    """Tests for MapEditGenerator."""

    @pytest.mark.asyncio
    async def test_generate_returns_output(self):
        gen = MapEditGenerator()
        inp = GeneratorInput(prompt="Add new tiles to farm")
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "edit_count" in out.metadata
        assert out.metadata["edit_count"] >= 1

    @pytest.mark.asyncio
    async def test_map_edits_file_generated(self):
        gen = MapEditGenerator()
        inp = GeneratorInput(prompt="Expand the farm map")
        out = await gen.generate(inp)
        assert "assets/data/map_edits.json" in out.files
        data = out.files["assets/data/map_edits.json"]
        assert "edits" in data
        assert len(data["edits"]) >= 1
        assert "EditType" in data["edits"][0]
        assert "TargetMap" in data["edits"][0]

    def test_validate_output_detects_missing_file(self):
        gen = MapEditGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("assets/data/map_edits.json missing" in e for e in errors)


class TestFarmExpansionContentJsonGenerator:
    """Tests for FarmExpansionContentJsonGenerator."""

    @pytest.mark.asyncio
    async def test_generate_assembles_content(self):
        gen = FarmExpansionContentJsonGenerator()
        inp = GeneratorInput(prompt="Create a farm expansion mod")
        # Set up prior outputs
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {
            "Format": "1.29.0",
            "UniqueID": "Test.FarmExpansion",
            "Name": "Test Farm Expansion",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        })

        building_out = GeneratorOutput()
        building_out.add_file("assets/data/buildings.json", {
            "buildings": [
                {
                    "BuildingName": "Test_Shed",
                    "BuildingType": "Shed",
                    "Width": 3,
                    "Height": 3,
                    "Description": "A test shed.",
                    "Cost": 300,
                    "Materials": [{"ItemName": "Wood", "Quantity": 100}],
                }
            ]
        })

        warp_out = GeneratorOutput()
        warp_out.add_file("assets/data/warps.json", {
            "warps": [
                {
                    "WarpName": "farm_to_test",
                    "FromMap": "Farm",
                    "FromX": 50,
                    "FromY": 10,
                    "ToMap": "TestArea",
                    "ToX": 5,
                    "ToY": 50,
                }
            ]
        })

        map_out = GeneratorOutput()
        map_out.add_file("assets/data/map_edits.json", {
            "edits": [
                {
                    "EditType": "AddTile",
                    "TargetMap": "Farm",
                    "Layer": "Back",
                    "X": 60,
                    "Y": 20,
                    "TileIndex": 0,
                    "TileSheet": "spring_outdoorsTileSheet",
                }
            ]
        })

        inp = GeneratorInput(
            prompt="Create a farm expansion mod",
            prior_outputs={
                "manifest_generator": manifest_out,
                "building_generator": building_out,
                "warp_point_generator": warp_out,
                "map_edit_generator": map_out,
            },
        )
        out = await gen.generate(inp)
        assert isinstance(out, GeneratorOutput)
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert isinstance(content, dict)
        assert "Changes" in content
        assert len(content["Changes"]) > 0
        assert out.metadata["mod_id"] == "test.farmexpansion"

    @pytest.mark.asyncio
    async def test_content_json_with_empty_prior(self):
        gen = FarmExpansionContentJsonGenerator()
        inp = GeneratorInput(prompt="farm expansion")
        inp["prior_outputs"] = {
            "manifest_generator": GeneratorOutput(),
            "building_generator": GeneratorOutput(),
            "warp_point_generator": GeneratorOutput(),
            "map_edit_generator": GeneratorOutput(),
        }
        out = await gen.generate(inp)
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert content["Changes"] == []

    def test_validate_output_detects_missing_content(self):
        gen = FarmExpansionContentJsonGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert any("content.json missing" in e for e in errors)

    def test_validate_output_detects_missing_changes(self):
        gen = FarmExpansionContentJsonGenerator()
        out = GeneratorOutput()
        out.add_file("content.json", {"Format": "1.29.0"})
        errors = gen.validate_output(out)
        assert any("Changes key missing" in e for e in errors)
