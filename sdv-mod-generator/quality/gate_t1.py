"""Tier 1 deterministic quality checks — real implementation.

T1 is the project's first quality gate, run between the generator
output aggregation node and the LLM-judge T2 panel. It enforces a
small set of *deterministic* invariants — file-level JSON / TSV
shape, Content Patcher manifest required fields, TSV header column
order — that a static analysis pass can verify without an LLM in the
loop. The gate's output (:class:`T1Result`) drives the LangGraph
conditional edge between T1 and T2 (T1 must pass before the
expensive 3-judge T2 panel is invoked).

Design notes:

* **Pure / no LLM.** The gate never calls ``llm.client.get_client``;
  if the LLM stack is misconfigured the gate still runs. That keeps
  it cheap (sub-millisecond per request) and gives operators a clean
  signal of "the schema is wrong" independent of "the LLM disagreed".
* **Fail-soft on empty outputs.** When the pipeline produced no
  generator outputs at all the gate emits the explicit
  ``t1_gate.no_generators`` error rather than raising — callers can
  branch on the message text without an exception class to import.
* **Per-generator specialisation.** ``_gen_specific_validation``
  adds the per-generator checks (manifest field set, shop TSV header,
  config schema presence, trigger-actions shape, mail-file presence,
  content.json action array). The generic file-level checks live in
  ``_validate_file`` and run for every file regardless of generator.
* **Content Patcher schema.** ``_validate_file`` additionally runs
  per-change CP-schema checks on ``content.json`` (When-token
  whitelist, no ``EditData`` + ``FromFile``, ``EditMap`` MapTiles
  must carry ``Position``) — catching the load-warning class the .test
  demo mods surfaced only at real-game load time.

The ``run_t1`` entry point is the only public symbol the rest of
the project is expected to import; everything else is internal and
may change without a deprecation cycle.
"""
import json
import structlog
from dataclasses import dataclass, field

from generators.base import GeneratorOutput

logger = structlog.get_logger(__name__)

#: Authoritative Content Patcher ``When`` condition keys, from CP source
#: ``ContentPatcher/Framework/Conditions/ConditionType.cs``. Matched
#: exactly; prefixed keys containing ``/`` (``Esca.EMP/...``,
#: ``<ModID>/...``) are accepted as mod-defined tokens.
VALID_CP_WHEN_TOKENS: frozenset[str] = frozenset({
    "Day", "DayEvent", "DayOfWeek", "DaysPlayed", "Season", "Year", "Weather",
    "HasActiveQuest", "HasCaughtFish", "HasCookingRecipe", "HasCraftingRecipe",
    "HasConversationTopic", "HasFlag", "HasProfession", "HasReadLetter",
    "HasSeenEvent", "HasVisitedLocation", "DailyLuck", "HasDialogueAnswer",
    "HasWalletItem", "IsMainPlayer", "IsOutdoors", "LocationContext",
    "LocationName", "LocationOwnerId", "LocationUniqueName", "PlayerGender",
    "PlayerName", "PreferredPet", "SkillLevel", "ChildNames", "ChildGenders",
    "Hearts", "Relationship", "Roommate", "Spouse", "FarmCave",
    "FarmhouseUpgrade", "FarmMapAsset", "FarmName", "FarmType",
    "IsCommunityCenterComplete", "IsJojaMartComplete", "HavingChild",
    "Pregnant", "Time", "Count", "Query", "Range", "Round", "Lowercase",
    "Merge", "PathPart", "Random", "Render", "Uppercase", "FirstValidFile",
    "HasMod", "HasFile", "HasValue", "I18n", "Language", "ModId",
    "AbsoluteFilePath", "FormatAssetName", "InternalAssetKey", "FromFile",
    "Target", "TargetWithoutPath", "TargetPathOnly",
})


@dataclass
class T1Result:
    passed: bool
    errors: list[str] = field(default_factory=list)


def run_t1(request_id: str, outputs: dict[str, GeneratorOutput]) -> T1Result:
    """Run Tier 1 deterministic checks.

    Validates:
    - All file contents are valid JSON (or TSV for data files)
    - manifest.json has all required Content Patcher fields
    - ConfigSchema field values are valid
    - Shop TSV has correct column structure
    - No empty required fields
    """
    logger.info("quality.t1.run", request_id=request_id, output_count=len(outputs))
    all_errors: list[str] = []

    if not outputs:
        all_errors.append("t1_gate.no_generators: pipeline produced no outputs")

    for gen_name, output in outputs.items():
        errors = _validate_generator_output(gen_name, output)
        all_errors.extend(errors)

    passed = len(all_errors) == 0
    if passed:
        logger.info("quality.t1.done", request_id=request_id, passed=True)
    else:
        logger.warning("quality.t1.done", request_id=request_id, passed=False, error_count=len(all_errors))

    return T1Result(passed=passed, errors=all_errors)


def _validate_generator_output(gen_name: str, output: GeneratorOutput) -> list[str]:
    errors: list[str] = []

    for file_path, content in output.files.items():
        file_errors = _validate_file(gen_name, file_path, content)
        errors.extend(file_errors)

    gen_specific = _gen_specific_validation(gen_name, output)
    errors.extend(gen_specific)

    return errors


def _validate_file(gen_name: str, file_path: str, content: dict | list | str) -> list[str]:
    errors: list[str] = []

    if file_path.endswith(".json"):
        if not isinstance(content, (dict, list)):
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if not isinstance(parsed, (dict, list)):
                        # Surface the parsed JSON's actual type (e.g.
                        # ``str``, ``int``) so operators can debug
                        # malformed generator output without re-running
                        # with a debugger. Mirrors the TSV branch's
                        # ``type(content).__name__`` disclosure below.
                        errors.append(
                            f"{gen_name}: {file_path} parsed but is not a JSON object or array "
                            f"(got {type(parsed).__name__})"
                        )
                    elif file_path == "content.json":
                        errors.extend(
                            f"{gen_name}: {file_path} {e}"
                            for e in _validate_content_json_schema(parsed)
                        )
                except json.JSONDecodeError:
                    errors.append(f"{gen_name}: {file_path} is not valid JSON")
            else:
                # Surface the actual Python type so operators can see at
                # a glance whether a generator returned an int / bool /
                # None / model instance instead of a dict / list. Without
                # this disclosure, a generator that emits ``42`` for a
                # JSON file surfaces the same opaque message as one that
                # emits ``False`` — both indistinguishable.
                errors.append(
                    f"{gen_name}: {file_path} is not a JSON object or array "
                    f"(got {type(content).__name__})"
                )
        elif file_path == "content.json":
            errors.extend(
                f"{gen_name}: {file_path} {e}"
                for e in _validate_content_json_schema(content)
            )
    elif file_path.endswith(".tsv"):
        if isinstance(content, str):
            # ``content.strip().split("\n")`` always yields >= 1 element
            # (even an empty/whitespace-only string splits to ``[""]``),
            # so the previous ``len(lines) < 1`` check was dead code and
            # silently let empty TSVs pass. Detect emptiness *before* the
            # split on the stripped string so the error path actually
            # fires for empty files.
            stripped = content.strip()
            if not stripped:
                errors.append(f"{gen_name}: {file_path} is empty")
        else:
            errors.append(f"{gen_name}: {file_path} expected TSV string, got {type(content).__name__}")

    return errors


def _cp_known_when_keys(content: dict | list) -> frozenset[str]:
    """Collect additional valid ``When`` keys declared in the content root.

    CP lets a change's ``When`` reference the mod's own ``ConfigSchema``
    field names and ``DynamicTokens`` names (the reference mod uses both,
    e.g. ``RealismMode`` / ``TVSNItemID``). These aren't ConditionType
    tokens, so the whitelist alone would false-positive on valid mods.
    """
    known: set[str] = set()
    if isinstance(content, dict):
        config = content.get("ConfigSchema")
        if isinstance(config, dict):
            known.update(config)
        dynamic_tokens = content.get("DynamicTokens")
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


def _validate_cp_change(change: dict, known_keys: frozenset[str]) -> list[str]:
    """Static Content Patcher schema checks for one content.json change.

    Catches the CP load-warning class the old gates missed (7/10 .test
    demo mods passed T1 yet produced CP load warnings at real-game load
    time): invalid ``When`` tokens, ``EditData`` + ``FromFile`` combos,
    and ``EditMap`` ``MapTiles`` entries missing ``Position``. Pure /
    no LLM, matching the gate's design contract.
    """
    errors: list[str] = []
    action_type = change.get("Action")

    when = change.get("When")
    if isinstance(when, dict):
        for token in when:
            if _is_valid_when_token(token, known_keys):
                continue
            errors.append(
                f"invalid CP When token '{token}' (not in ConditionType whitelist)"
            )

    if action_type == "EditData" and "FromFile" in change:
        errors.append(
            "'EditData' can't have 'FromFile' (FromFile is only valid on Load/EditImage)"
        )

    if action_type == "EditMap":
        map_tiles = change.get("MapTiles")
        if isinstance(map_tiles, list):
            for j, tile in enumerate(map_tiles):
                if isinstance(tile, dict) and not tile.get("Position"):
                    errors.append(f"EditMap MapTiles[{j}] missing 'Position'")

    return errors


def _validate_content_json_schema(content: dict | list) -> list[str]:
    """Run :func:`_validate_cp_change` over every change in a content.json root.

    Accepts both the CP 2.x object root (``Format`` / ``Changes``) and
    the legacy CP 1.x bare-array root. Errors carry the change index so
    operators can locate the offending action.
    """
    errors: list[str] = []
    known_keys = _cp_known_when_keys(content)
    if isinstance(content, dict):
        changes = content.get("Changes")
        if isinstance(changes, list):
            for i, change in enumerate(changes):
                if isinstance(change, dict):
                    errors.extend(
                        f"Changes[{i}] {e}" for e in _validate_cp_change(change, known_keys)
                    )
    elif isinstance(content, list):
        for i, change in enumerate(content):
            if isinstance(change, dict):
                errors.extend(
                    f"content.json[{i}] {e}" for e in _validate_cp_change(change, known_keys)
                )
    return errors


def _gen_specific_validation(gen_name: str, output: GeneratorOutput) -> list[str]:
    errors: list[str] = []

    if gen_name == "manifest_generator":
        manifest = output.files.get("manifest.json", {})
        # Guard against non-dict manifest content. The ``field_name not in
        # manifest`` membership test would raise ``TypeError`` if a generator
        # emitted an int / list / None for ``manifest.json`` — that crash
        # would short-circuit the gate and hide the per-field error report
        # operators need to fix the generator. Surface a clear
        # type-disclosure error instead, matching the v48 pattern in
        # ``_validate_file`` and the ``config_schema_generator`` arm below.
        if not isinstance(manifest, dict):
            errors.append(
                f"manifest_generator: manifest.json is not a JSON object "
                f"(got {type(manifest).__name__})"
            )
        else:
            required = ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]
            for field_name in required:
                if field_name not in manifest:
                    errors.append(f"manifest_generator: missing required field '{field_name}'")
            # ``ContentPackFor`` membership on a dict is now safe (guarded
            # above), but keep the inner ``isinstance(cpf, dict)`` check for
            # forward compatibility — a generator that sets
            # ``ContentPackFor`` to a list should still get a precise
            # error, not a TypeError on the nested ``UniqueID`` lookup.
            if "ContentPackFor" in manifest:
                cpf = manifest["ContentPackFor"]
                if isinstance(cpf, dict) and "UniqueID" not in cpf:
                    errors.append("manifest_generator: ContentPackFor.UniqueID missing")

    elif gen_name == "shop_item_pool_generator":
        shops_tsv = output.files.get("assets/data/shops.tsv", "")
        if isinstance(shops_tsv, str):
            lines = shops_tsv.strip().split("\n")
            if len(lines) < 2:
                errors.append("shop_item_pool_generator: assets/data/shops.tsv has no data rows")
            else:
                header = lines[0].split("\t")
                expected = ["ItemType", "ItemName", "ItemName2", "Price", "Stock"]
                if header != expected:
                    errors.append(f"shop_item_pool_generator: assets/data/shops.tsv header mismatch — expected {expected}, got {header}")

    elif gen_name == "config_schema_generator":
        config = output.files.get("config.json", {})
        # Guard against non-dict config content. ``"Enabled" not in config``
        # raises ``TypeError`` on an int / list / None — that crash would
        # bypass the gate's error reporting. Surface a clear type-disclosure
        # error instead, matching the v48 pattern in ``_validate_file``.
        if not isinstance(config, dict):
            errors.append(
                f"config_schema_generator: config.json is not a JSON object "
                f"(got {type(config).__name__})"
            )
        elif "Enabled" not in config:
            errors.append("config_schema_generator: config.json missing 'Enabled' field")

    elif gen_name == "trigger_logic_generator":
        triggers = output.files.get("data/trigger_actions.json", {})
        # ``if not triggers`` is permissive — an int 0, empty string, or
        # None all count as "missing or empty", but a *non-empty* list of
        # ints would silently pass as "present and well-formed". Require
        # a non-empty dict so the gate's contract matches the rest of the
        # JSON-object generators (``manifest_generator``, ``config_schema_generator``).
        if not isinstance(triggers, dict) or not triggers:
            errors.append("trigger_logic_generator: data/trigger_actions.json missing or empty")

    elif gen_name == "mail_system_generator":
        mail_files = [v for k, v in output.files.items() if k.startswith("mail/")]
        if not mail_files:
            errors.append("mail_system_generator: no mail files generated")

    elif gen_name == "content_json_generator":
        content = output.files.get("content.json")
        if not content:
            errors.append("content_json_generator: content.json missing")
        elif isinstance(content, dict):
            # CP 2.x object root — the shape every generator pack emits.
            # The pre-fix gate only accepted a bare list, which both
            # codified shop_channel's malformed output AND rejected the
            # CP 2.x object root the reference mod uses.
            if "Format" not in content:
                errors.append("content_json_generator: content.json object root missing 'Format'")
            changes = content.get("Changes")
            if not isinstance(changes, list):
                errors.append("content_json_generator: content.json object root missing 'Changes' array")
            else:
                for i, action in enumerate(changes):
                    if not isinstance(action, dict):
                        errors.append(f"content_json_generator: content.json Changes[{i}] is not an object")
                    elif "Action" not in action:
                        errors.append(f"content_json_generator: content.json Changes[{i}] missing 'Action' field")
        elif isinstance(content, list):
            # Legacy CP 1.x bare-array root — tolerated but deprecated.
            for i, action in enumerate(content):
                if not isinstance(action, dict):
                    errors.append(f"content_json_generator: content.json[{i}] is not an object")
                elif "Action" not in action:
                    errors.append(f"content_json_generator: content.json[{i}] missing 'Action' field")
        else:
            errors.append(
                f"content_json_generator: content.json must be a JSON object or array "
                f"(got {type(content).__name__})"
            )

    return errors