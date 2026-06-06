"""Tests for the SMAPI manifest validator."""
import json
import zipfile

import pytest

from tests.smapi_validate import validate_manifest, validate_content_json, validate_zip_contents


def _make_zip(tmp_path, files: dict) -> "Path":
    p = tmp_path / "mod.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for name, content in files.items():
            if isinstance(content, (dict, list)):
                zf.writestr(name, json.dumps(content))
            else:
                zf.writestr(name, content)
    return p


class TestValidateManifest:
    def test_valid_manifest(self):
        errors = validate_manifest({
            "Format": "1.29.0",
            "Name": "Test Mod",
            "Author": "Tester",
            "Version": "1.0.0",
            "UniqueID": "Tester.TestMod",
        })
        assert errors == []

    def test_missing_required_field(self):
        errors = validate_manifest({"Name": "X", "Author": "Y", "Version": "1.0.0"})
        assert any("UniqueID" in e for e in errors)

    def test_invalid_unique_id(self):
        errors = validate_manifest({
            "Name": "X", "Author": "Y", "Version": "1.0.0", "UniqueID": "bad id with spaces!",
        })
        assert any("UniqueID" in e for e in errors)

    def test_invalid_version(self):
        errors = validate_manifest({
            "Name": "X", "Author": "Y", "Version": "1.0", "UniqueID": "OK.ID",
        })
        assert any("Version" in e for e in errors)

    def test_long_name_truncation_warning(self):
        errors = validate_manifest({
            "Name": "X" * 150, "Author": "Y", "Version": "1.0.0", "UniqueID": "OK.ID",
        })
        assert any("Name" in e for e in errors)


class TestValidateContentJson:
    def test_valid_actions(self):
        errors = validate_content_json([
            {"Action": "Load", "Target": "Data/X", "FromFile": "x.json"},
            {"Action": "EditData", "Target": "Data/Y", "Entries": {"a": 1}},
        ])
        assert errors == []

    def test_not_an_array(self):
        errors = validate_content_json({"Action": "Load"})
        assert any("array" in e for e in errors)

    def test_unknown_action(self):
        errors = validate_content_json([{"Action": "DoSomething"}])
        assert any("unknown Action" in e for e in errors)

    def test_load_missing_fromfile(self):
        errors = validate_content_json([{"Action": "Load", "Target": "Data/X"}])
        assert any("FromFile" in e for e in errors)

    def test_editdata_missing_target(self):
        errors = validate_content_json([{"Action": "EditData", "Entries": {}}])
        assert any("Target" in e for e in errors)


class TestValidateZipContents:
    def test_valid_mod_passes(self, tmp_path):
        p = _make_zip(tmp_path, {
            "manifest.json": {
                "Format": "1.29.0",
                "Name": "Test",
                "Author": "T",
                "Version": "1.0.0",
                "UniqueID": "T.Test",
            },
            "content.json": [
                {"Action": "Load", "Target": "Data/X", "FromFile": "x.json"},
            ],
            "x.json": '{"key":"value"}',
        })
        errors = validate_zip_contents(p)
        assert errors == [], errors

    def test_missing_manifest(self, tmp_path):
        p = _make_zip(tmp_path, {"content.json": "[]"})
        errors = validate_zip_contents(p)
        assert any("manifest.json" in e for e in errors)

    def test_contentjson_fromfile_not_in_zip(self, tmp_path):
        p = _make_zip(tmp_path, {
            "manifest.json": {
                "Name": "X", "Author": "Y", "Version": "1.0.0", "UniqueID": "X.Y",
            },
            "content.json": [
                {"Action": "Load", "Target": "X", "FromFile": "missing.json"},
            ],
        })
        errors = validate_zip_contents(p)
        assert any("missing.json" in e and "not in the zip" in e for e in errors)

    def test_i18n_must_be_valid_json(self, tmp_path):
        p = _make_zip(tmp_path, {
            "manifest.json": {
                "Name": "X", "Author": "Y", "Version": "1.0.0", "UniqueID": "X.Y",
            },
            "i18n/default.json": "{not valid json",
        })
        errors = validate_zip_contents(p)
        assert any("i18n/default.json" in e for e in errors)

    def test_backslash_in_path_caught(self, tmp_path):
        p = _make_zip(tmp_path, {
            "manifest.json": {
                "Name": "X", "Author": "Y", "Version": "1.0.0", "UniqueID": "X.Y",
            },
            "back\\slash.json": "{}",
        })
        errors = validate_zip_contents(p)
        assert any("backslash" in e for e in errors)
