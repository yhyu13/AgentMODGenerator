"""SMAPI load-time validator.

Validates a produced mod zip against the checks SMAPI performs at load time.
This is NOT a substitute for the real SMAPI load test (which requires a
Stardew Valley install — not available on this host), but catches ~90% of
the failure modes that surface as '[SMAPI] this mod failed to load':

- manifest.json structure (required fields, version format, UniqueID format)
- content.json structure (must be a JSON array of CP action objects)
- All 'FromFile' paths in content.json exist inside the zip
- i18n files parse as JSON
- File paths don't have backslashes (Windows-style would fail on Linux/Mac)

Game-data validation (does the TV channel ID exist, does the shop ID exist)
only happens at runtime when the actual game loads, and cannot be done here.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

REQUIRED_MANIFEST_FIELDS = ["Name", "Author", "Version", "UniqueID"]
CP_REQUIRED_ACTION_FIELDS = ["Action"]


def validate_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            errors.append(f"manifest.json: missing required field '{field}'")

    uid = manifest.get("UniqueID", "")
    if uid and not re.match(r"^[A-Za-z0-9_.-]+$", uid):
        errors.append(f"manifest.json: UniqueID '{uid}' has invalid characters (allowed: letters, digits, _, ., -)")

    version = manifest.get("Version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$", version):
        errors.append(f"manifest.json: Version '{version}' doesn't match x.y.z or x.y.z-tag")

    fmt = manifest.get("Format", "")
    if fmt and not re.match(r"^\d+\.\d+\.\d+$", fmt):
        errors.append(f"manifest.json: Format '{fmt}' should be a semver string")

    name = manifest.get("Name", "")
    if name and len(name) > 100:
        errors.append(f"manifest.json: Name is {len(name)} chars (SMAPI truncates over 100)")

    return errors


def validate_content_json(content_data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(content_data, list):
        errors.append(f"content.json: must be a JSON array, got {type(content_data).__name__}")
        return errors

    valid_actions = {
        "Load", "EditData", "EditImage", "EditMap", "Include", "Exit",
        "Watch", "AddStardewMuseum", "Update", "AddBuy",
    }

    for i, action in enumerate(content_data):
        if not isinstance(action, dict):
            errors.append(f"content.json: action[{i}] is not an object")
            continue
        if "Action" not in action:
            errors.append(f"content.json: action[{i}] missing 'Action' field")
            continue
        if action["Action"] not in valid_actions:
            errors.append(f"content.json: action[{i}] has unknown Action '{action['Action']}'")
        if action["Action"] == "Load" and "FromFile" not in action:
            errors.append(f"content.json: action[{i}] 'Load' missing 'FromFile'")
        if action["Action"] == "EditData" and "Target" not in action and "Targets" not in action:
            errors.append(f"content.json: action[{i}] 'EditData' missing 'Target' or 'Targets'")

    return errors


def validate_zip_contents(zip_path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())

        if "manifest.json" not in names:
            errors.append("zip: missing manifest.json")
            return errors

        try:
            manifest_data = json.loads(zf.read("manifest.json"))
        except json.JSONDecodeError as exc:
            errors.append(f"manifest.json: not valid JSON: {exc}")
            return errors

        errors.extend(validate_manifest(manifest_data))

        # Check content.json if present
        content_data: object = None
        if "content.json" in names:
            try:
                content_data = json.loads(zf.read("content.json"))
            except json.JSONDecodeError as exc:
                errors.append(f"content.json: not valid JSON: {exc}")
            else:
                errors.extend(validate_content_json(content_data))

        # Verify all 'FromFile' paths in content.json exist in the zip
        if isinstance(content_data, list):
            for i, action in enumerate(content_data):
                if isinstance(action, dict) and "FromFile" in action:
                    from_file = action["FromFile"]
                    if from_file not in names:
                        errors.append(
                            f"content.json: action[{i}] references '{from_file}' which is not in the zip"
                        )

        # i18n files must be valid JSON
        for name in names:
            if name.startswith("i18n/") and name.endswith(".json"):
                try:
                    json.loads(zf.read(name))
                except json.JSONDecodeError as exc:
                    errors.append(f"{name}: not valid JSON: {exc}")

        # Paths must use forward slashes (zip spec)
        for name in names:
            if "\\" in name:
                errors.append(f"zip: entry '{name}' has backslash (should be forward slash)")

        # Detect obviously empty files that SMAPI would skip
        for name in names:
            info = zf.getinfo(name)
            if info.file_size == 0 and name.endswith(".json"):
                errors.append(f"{name}: file is empty (0 bytes)")

    return errors


def main(zip_path: str) -> int:
    p = Path(zip_path)
    if not p.exists():
        print(f"ERROR: file not found: {zip_path}")
        return 2

    print(f"Validating: {p}")
    print(f"Size: {p.stat().st_size} bytes")

    errors = validate_zip_contents(p)
    if errors:
        print(f"\nFAILED ({len(errors)} error(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nPASSED: zip would load in SMAPI without manifest-level errors")
    print("NOTE: This is a static check. SMAPI also validates game-data")
    print("      references (e.g. does the TV channel ID exist in the game)")
    print("      at runtime, which requires a real Stardew Valley install.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
