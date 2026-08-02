"""Golden test: the reference mod must pass our own validator.

Pins ``tests/smapi_validate.py`` to reality. The reference mod
(``.reference_mods/TV Shopping Network/``) is the product's own MVP bar —
if the validator rejects it, the validator encodes an outdated model of
Content Patcher and every generated mod is judged against a wrong
contract (the "your own MVP bar would FAIL your own gate" failure from
the deepseek MVP audit).

The reference mod uses the CP 2.x object root (``Format`` +
``ConfigSchema`` + ``DynamicTokens`` + ``Changes``) with tokenized
``FromFile`` paths (``Assets/Items/item_{{...}}.png``) and ``@``-style
dynamic references — all of which the validator must accept.
"""
import json
import zipfile
from pathlib import Path

from tests.smapi_validate import (
    validate_content_json,
    validate_manifest,
    validate_zip_contents,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCE_DIR = REPO_ROOT / ".reference_mods" / "TV Shopping Network"


def _require_reference() -> bool:
    return REFERENCE_DIR.exists() and (REFERENCE_DIR / "content.json").exists()


def _build_reference_zip(tmp_path) -> Path:
    p = tmp_path / "reference_mod.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in REFERENCE_DIR.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(REFERENCE_DIR).as_posix())
    return p


class TestReferenceManifest:
    def test_reference_manifest_passes(self):
        if not _require_reference():
            import pytest
            pytest.skip("reference mod not present in checkout")
        manifest = json.loads((REFERENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
        errors = validate_manifest(manifest)
        assert errors == [], errors

    def test_reference_manifest_has_dependencies(self):
        if not _require_reference():
            import pytest
            pytest.skip("reference mod not present in checkout")
        manifest = json.loads((REFERENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
        assert isinstance(manifest.get("Dependencies"), list)
        assert manifest["Dependencies"], "reference manifest must declare its dependency DLLs"


class TestReferenceContentJson:
    def test_reference_content_is_object_root(self):
        if not _require_reference():
            import pytest
            pytest.skip("reference mod not present in checkout")
        content = json.loads((REFERENCE_DIR / "content.json").read_text(encoding="utf-8"))
        assert isinstance(content, dict)
        assert "Format" in content
        assert isinstance(content.get("Changes"), list)

    def test_reference_content_passes_validator(self):
        if not _require_reference():
            import pytest
            pytest.skip("reference mod not present in checkout")
        content = json.loads((REFERENCE_DIR / "content.json").read_text(encoding="utf-8"))
        errors = validate_content_json(content)
        assert errors == [], errors


class TestReferenceZip:
    def test_reference_zip_passes_end_to_end(self, tmp_path):
        if not _require_reference():
            import pytest
            pytest.skip("reference mod not present in checkout")
        zip_path = _build_reference_zip(tmp_path)
        errors = validate_zip_contents(zip_path)
        assert errors == [], errors
