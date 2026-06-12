"""Tests for packager README generation and manifest validation."""
import json
import zipfile
from pathlib import Path
from generators.packager import (
    package, _build_readme, read_zip, validate_manifest, package_with_validation,
)


class TestBuildReadme:
    """Tests for _build_readme helper."""

    def test_includes_request_id(self) -> None:
        """README should include the request ID."""
        readme = _build_readme("req-123", {"content.json": {}})
        assert "Mod Request ID: req-123" in readme

    def test_includes_file_list(self) -> None:
        """README should list all files in the mod."""
        files = {"content.json": {}, "manifest.json": {}}
        readme = _build_readme("req-456", files)
        assert "content.json" in readme
        assert "manifest.json" in readme

    def test_has_installation_instructions(self) -> None:
        """README should contain installation instructions."""
        readme = _build_readme("req-789", {})
        assert "Installation Instructions:" in readme
        assert "SMAPI" in readme
        assert "Content Patcher" in readme


class TestPackageReadme:
    """Tests that README.txt is included in packaged zips."""

    def test_package_includes_readme(self, tmp_path, monkeypatch) -> None:
        """Package should include a README.txt file."""
        monkeypatch.setattr("generators.packager._LOCAL_OUTPUT_DIR", str(tmp_path))
        files = {"content.json": {"Changes": []}, "manifest.json": {"Name": "Test"}}
        zip_key = package("test-req-1", files, [])

        zip_path = tmp_path / zip_key
        assert zip_path.exists()

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "README.txt" in names
            readme_content = zf.read("README.txt").decode("utf-8")
            assert "test-req-1" in readme_content
            assert "content.json" in readme_content


class TestManifestValidation:
    """Tests for manifest validation."""

    def test_valid_manifest_passes(self) -> None:
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "Author.ModName",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert errors == []

    def test_missing_unique_id_fails(self) -> None:
        manifest = {
            "Format": "1.29.0",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert any("UniqueID" in e for e in errors)

    def test_missing_content_pack_for_fails(self) -> None:
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "Author.ModName",
            "Name": "Test Mod",
            "Version": "1.0.0",
        }
        errors = validate_manifest(manifest)
        assert any("ContentPackFor" in e for e in errors)

    def test_invalid_unique_id_no_dot_warns(self) -> None:
        manifest = {
            "Format": "1.29.0",
            "UniqueID": "NoDot",
            "Name": "Test Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        }
        errors = validate_manifest(manifest)
        assert any("dot" in e.lower() for e in errors)

    def test_content_pack_for_missing_unique_id_fails(self) -> None:
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
    """Tests for package_with_validation."""

    def test_adds_i18n_skeleton(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
        files = {
            "manifest.json": {"Format": "1.29.0", "UniqueID": "Author.Mod", "Name": "Test", "Version": "1.0.0", "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"}},
            "content.json": {"Changes": []},
        }
        zip_key = package_with_validation("test-req-i18n", files, [])
        result = read_zip(zip_key)
        assert "i18n/default.json" in result

    def test_validates_manifest_and_warns(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
        files = {
            "manifest.json": {"Name": "Bad Manifest"},
            "content.json": {"Changes": []},
        }
        zip_key = package_with_validation("test-req-bad", files, [])
        result = read_zip(zip_key)
        assert "README.txt" in result
