"""Tests for farm_expansion routing and packager improvements."""

from orchestrator.router import route, detect_game
from generators.packager import validate_manifest, package_with_validation


class TestFarmExpansionRouting:
    def test_farm_expansion_keyword_routing(self):
        phase, hint = route("create a farm expansion with new buildings")
        assert phase == "farm_expansion"
        assert hint["game"] == "stardew_valley"
        assert "building_generator" in hint["generators"]
        assert "warp_point_generator" in hint["generators"]
        assert "map_edit_generator" in hint["generators"]
        assert "farm_expansion_content_json_generator" in hint["generators"]

    def test_building_keyword_routing(self):
        phase, hint = route("add custom buildings to stardew valley")
        assert phase == "farm_expansion"
        assert "building_generator" in hint["generators"]

    def test_warp_keyword_routing(self):
        phase, hint = route("add warp points to my farm")
        assert phase == "farm_expansion"
        assert "warp_point_generator" in hint["generators"]

    def test_map_edit_keyword_routing(self):
        phase, hint = route("map edit for farm")
        assert phase == "farm_expansion"
        assert "map_edit_generator" in hint["generators"]

    def test_new_area_keyword_routing(self):
        phase, hint = route("create a new area on my farm")
        assert phase == "farm_expansion"

    def test_farm_expansion_execution_order(self):
        phase, hint = route("farm expansion mod")
        order = hint["execution_order"]
        assert order.index("manifest_generator") < order.index("building_generator")
        assert order.index("building_generator") < order.index("warp_point_generator")
        assert order.index("warp_point_generator") < order.index("map_edit_generator")
        assert order.index("map_edit_generator") < order.index("farm_expansion_content_json_generator")


class TestManifestValidation:
    def test_valid_manifest_passes(self):
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "Author.ModName",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert errors == []

    def test_missing_unique_id_fails(self):
        manifest = {
            "Format": "1.29.0",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert any("UniqueID" in e for e in errors)

    def test_missing_content_pack_for_fails(self):
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "Author.ModName",
            "Name": "Test Mod",
            "Version": "1.0.0",
        }
        errors = validate_manifest(manifest)
        assert any("ContentPackFor" in e for e in errors)

    def test_invalid_unique_id_no_dot_warns(self):
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "NoDot",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert any("dot" in e.lower() for e in errors)

    def test_content_pack_for_missing_unique_id_fails(self):
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "Author.ModName",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {},
        }
        errors = validate_manifest(manifest)
        assert any("ContentPackFor" in e for e in errors)


class TestPackageWithValidation:
    def test_adds_i18n_skeleton(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
        files = {
            "manifest.json": {"Format": "1.29.0", "UniqueID": "Author.Mod", "Name": "Test", "Version": "1.0.0", "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"}},
            "content.json": {"Changes": []},
        }
        zip_key = package_with_validation("test-req-i18n", files, [])
        from generators.packager import read_zip
        result = read_zip(zip_key)
        assert "i18n/default.json" in result

    def test_validates_manifest_and_warns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
        files = {
            "manifest.json": {"Name": "Bad Manifest"},  # missing required fields
            "content.json": {"Changes": []},
        }
        # Should still package despite validation errors
        zip_key = package_with_validation("test-req-bad", files, [])
        from generators.packager import read_zip
        result = read_zip(zip_key)
        assert "README.txt" in result
