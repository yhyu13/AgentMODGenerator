"""SMAPI load-time validator.

Validates a produced mod zip against the checks SMAPI performs at load time.
This is NOT a substitute for the real SMAPI load test (which requires a
Stardew Valley install — not available on this host), but catches ~90% of
the failure modes that surface as '[SMAPI] this mod failed to load':

- manifest.json structure (required fields, version format, UniqueID format)
- content.json structure — accepts BOTH Content Patcher roots:
  - CP 2.x object root: ``{"Format": ..., "ConfigSchema": ..., "Changes": [...]}``
    (the format emitted by every generator pack and the reference mod
    ``.reference_mods/TV Shopping Network/``)
  - CP 1.x legacy array root: ``[{Action: ...}, ...]``
- content.json per-change CP-schema checks: ``When`` keys must be known
  ConditionType tokens (shared whitelist from ``quality.gate_t1``),
  the mod's own ConfigSchema fields / DynamicTokens, mod-prefixed
  keys (``ModID/...``) or token-with-args keys (``Random:...``);
  ``EditData`` actions can't carry ``FromFile``, and ``EditMap``
  ``MapTiles`` entries must include ``Position``
- All 'FromFile' paths in content.json exist inside the zip (tokenized
  paths containing ``{{...}}`` are treated as dynamic and skipped)
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

from quality.gate_t1 import VALID_CP_WHEN_TOKENS

REQUIRED_MANIFEST_FIELDS = ["Name", "Author", "Version", "UniqueID"]
CP_REQUIRED_ACTION_FIELDS = ["Action"]

#: Tokenized FromFile paths (``Assets/Items/item_{{TVSNRandomItem}}.png``)
#: reference runtime-resolved tokens — the file may not exist literally in
#: the zip, so the existence check must skip them.
_TOKEN_REFERENCE_RE = re.compile(r"\{\{")


def _strip_json_comments(text: str) -> str:
    """Strip ``//`` and ``/* ... */`` comments from JSON text.

    SMAPI / Content Patcher parse JSON with comments enabled (Json.NET
    ``CommentHandling``), and the reference mod's own ``i18n/default.json``
    carries ``//For translators:`` comments — so strict ``json.loads``
    would fail the product's own MVP bar. Only comments outside string
    literals are removed.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_string:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize_jsonc(text: str) -> str:
    """Make SMAPI-style JSON (comments + trailing commas) strict-JSON parseable."""
    stripped = _strip_json_comments(text)
    # Drop trailing commas before } or ] (outside string literals).
    out: list[str] = []
    i = 0
    n = len(stripped)
    in_string = False
    while i < n:
        ch = stripped[i]
        nxt = stripped[i + 1] if i + 1 < n else ""
        if in_string:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and stripped[j] in " \t\r\n":
                j += 1
            if j < n and stripped[j] in "}]":
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_json(text: str):
    """Parse JSON with SMAPI-style comment and trailing-comma support."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_normalize_jsonc(text))


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


def _cp_known_when_keys(content_data: object) -> frozenset[str]:
    """Collect additional valid ``When`` keys declared in the content root.

    CP lets a change's ``When`` reference the mod's own ``ConfigSchema``
    field names and ``DynamicTokens`` names (the reference mod uses both,
    e.g. ``RealismMode`` / ``TVSNItemID``). These aren't ConditionType
    tokens, so the whitelist alone would false-positive on valid mods.
    """
    known: set[str] = set()
    if isinstance(content_data, dict):
        config = content_data.get("ConfigSchema")
        if isinstance(config, dict):
            known.update(config)
        dynamic_tokens = content_data.get("DynamicTokens")
        if isinstance(dynamic_tokens, list):
            for entry in dynamic_tokens:
                if isinstance(entry, dict):
                    name = entry.get("Name")
                    if isinstance(name, str):
                        known.add(name)
    return frozenset(known)


def _is_valid_when_token(token: str, known_keys: frozenset[str]) -> bool:
    """Whether a ``When`` key is a valid CP condition reference."""
    if "/" in token:  # mod-defined token (``Esca.EMP/...``, ``<ModID>/...``)
        return True
    if token in VALID_CP_WHEN_TOKENS:
        return True
    if token in known_keys:  # ConfigSchema field / DynamicToken name
        return True
    # Token-with-arguments form (``Random:{{Range:1,20}}``).
    base, sep, _ = token.partition(":")
    return bool(sep) and base in VALID_CP_WHEN_TOKENS


def _validate_actions(actions: list, errors: list[str], known_when_keys: frozenset[str] = frozenset()) -> None:
    """Validate a list of CP change objects (shared by both root shapes)."""
    valid_actions = {
        "Load", "EditData", "EditImage", "EditMap", "Include", "Exit",
        "Watch", "AddStardewMuseum", "Update", "AddBuy",
    }

    for i, action in enumerate(actions):
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
        # CP load-warning class that only surfaced at real-game load time
        # (7/10 .test demo mods): invalid When tokens, EditData+FromFile,
        # and EditMap MapTiles without Position.
        when = action.get("When")
        if isinstance(when, dict):
            for token in when:
                if _is_valid_when_token(token, known_when_keys):
                    continue
                errors.append(
                    f"content.json: action[{i}] has invalid CP When token '{token}'"
                )
        if action["Action"] == "EditData" and "FromFile" in action:
            errors.append(
                f"content.json: action[{i}] 'EditData' can't have 'FromFile' "
                "(FromFile is only valid on Load/EditImage)"
            )
        if action["Action"] == "EditMap":
            map_tiles = action.get("MapTiles")
            if isinstance(map_tiles, list):
                for j, tile in enumerate(map_tiles):
                    if not isinstance(tile, dict):
                        continue
                    pos = tile.get("Position")
                    if not pos:
                        errors.append(
                            f"content.json: action[{i}] 'EditMap' MapTiles[{j}] missing 'Position'"
                        )
                    elif not isinstance(pos, dict) or "X" not in pos or "Y" not in pos:
                        errors.append(
                            f"content.json: action[{i}] 'EditMap' MapTiles[{j}] "
                            "'Position' must be an object with 'X' and 'Y' "
                            f"(got {pos!r})"
                        )


def validate_content_json(content_data: object) -> list[str]:
    """Validate content.json against the Content Patcher shape.

    Accepts the CP 2.x object root (``Format`` + ``Changes``) AND the
    legacy CP 1.x array root (a bare list of actions). The old array-only
    model rejected the real CP 2.x format — the reference mod
    (``.reference_mods/TV Shopping Network/``) is an object root, so the
    validator previously failed the product's own MVP bar.
    """
    errors: list[str] = []
    known_when_keys = _cp_known_when_keys(content_data)

    if isinstance(content_data, dict):
        changes = content_data.get("Changes")
        if not isinstance(changes, list):
            errors.append("content.json: object root missing 'Changes' array")
            return errors
        if "Format" not in content_data:
            errors.append("content.json: object root missing 'Format' field")
        _validate_actions(changes, errors, known_when_keys)
        return errors

    if not isinstance(content_data, list):
        errors.append(f"content.json: must be a JSON object (Format/Changes) or array, got {type(content_data).__name__}")
        return errors

    _validate_actions(content_data, errors, known_when_keys)
    return errors


def _iter_actions(content_data: object):
    """Yield every action dict in either root shape."""
    if isinstance(content_data, dict):
        changes = content_data.get("Changes", [])
        if isinstance(changes, list):
            for action in changes:
                yield action
    elif isinstance(content_data, list):
        for action in content_data:
            yield action


def _from_file_exists(from_file: str, names: set[str]) -> bool:
    """Resolve a CP FromFile path against the zip's file list.

    Handles the ``@/`` mod-relative prefix (CP token pointing at the mod's
    own folder) and skips tokenized paths entirely.
    """
    if _TOKEN_REFERENCE_RE.search(from_file):
        return True
    path = from_file.removeprefix("@/").removeprefix("/")
    return path in names


def _validate_entry_names(names: set[str]) -> list[str]:
    """Check zip entry names for Windows-style path artifacts.

    Zip entries must use forward slashes per the zip spec; a backslash
    (zips written by Windows tools) or a doubled separator (the artifact
    of normalizing backslashes twice) breaks SMAPI on Linux/Mac.
    """
    errors: list[str] = []
    for name in names:
        if "\\" in name:
            errors.append(f"zip: entry '{name}' has backslash (should be forward slash)")
        elif "//" in name:
            errors.append(f"zip: entry '{name}' has doubled separator (Windows path artifact)")
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
                content_data = _loads_json(zf.read("content.json").decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                errors.append(f"content.json: not valid JSON: {exc}")
            else:
                errors.extend(validate_content_json(content_data))

        # Verify all 'FromFile' paths in content.json exist in the zip
        for i, action in enumerate(_iter_actions(content_data)):
            if isinstance(action, dict) and "FromFile" in action:
                from_file = action["FromFile"]
                if not _from_file_exists(from_file, names):
                    errors.append(
                        f"content.json: action[{i}] references '{from_file}' which is not in the zip"
                    )

        # i18n files must be valid JSON (SMAPI tolerates comments)
        for name in names:
            if name.startswith("i18n/") and name.endswith(".json"):
                try:
                    _loads_json(zf.read(name).decode("utf-8", errors="replace"))
                except json.JSONDecodeError as exc:
                    errors.append(f"{name}: not valid JSON: {exc}")

        # Paths must use forward slashes (zip spec)
        errors.extend(_validate_entry_names(names))

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
