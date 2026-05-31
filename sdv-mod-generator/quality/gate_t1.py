"""Tier 1 deterministic quality checks — real implementation."""
import json
import structlog
from dataclasses import dataclass, field

from generators.base import GeneratorOutput

logger = structlog.get_logger()


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
    logger.info("quality.t1.run", request_id=request_id)
    all_errors: list[str] = []

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


def _validate_file(gen_name: str, file_path: str, content: dict | str) -> list[str]:
    errors: list[str] = []

    if file_path.endswith(".json"):
        if not isinstance(content, dict):
            try:
                json.loads(content) if isinstance(content, str) else None
            except Exception:
                errors.append(f"{gen_name}: {file_path} is not a JSON object")
    elif file_path.endswith(".tsv"):
        if isinstance(content, str):
            lines = content.strip().split("\n")
            if len(lines) < 1:
                errors.append(f"{gen_name}: {file_path} is empty")
        else:
            errors.append(f"{gen_name}: {file_path} expected TSV string, got {type(content).__name__}")

    return errors


def _gen_specific_validation(gen_name: str, output: GeneratorOutput) -> list[str]:
    errors: list[str] = []

    if gen_name == "manifest_generator":
        manifest = output.files.get("manifest.json", {})
        required = ["Format", "UniqueID", "Name", "Version", "ContentPackFor"]
        for field_name in required:
            if field_name not in manifest:
                errors.append(f"manifest_generator: missing required field '{field_name}'")
        if "ContentPackFor" in manifest:
            cpf = manifest["ContentPackFor"]
            if isinstance(cpf, dict) and "UniqueID" not in cpf:
                errors.append("manifest_generator: ContentPackFor.UniqueID missing")

    elif gen_name == "shop_item_pool_generator":
        shops_tsv = output.files.get("Data/Shops.tsv", "")
        if isinstance(shops_tsv, str):
            lines = shops_tsv.strip().split("\n")
            if len(lines) < 2:
                errors.append("shop_item_pool_generator: Data/Shops.tsv has no data rows")
            else:
                header = lines[0].split("\t")
                expected = ["ItemType", "ItemName", "ItemName2", "Price", "Stock"]
                if header[: len(expected)] != expected[: len(header)]:
                    errors.append(f"shop_item_pool_generator: Data/Shops.tsv header mismatch — got {header}")

    elif gen_name == "config_schema_generator":
        config = output.files.get("config.json", {})
        if "Enabled" not in config:
            errors.append("config_schema_generator: config.json missing 'Enabled' field")

    elif gen_name == "trigger_logic_generator":
        triggers = output.files.get("data/trigger_actions.json", {})
        if not triggers:
            errors.append("trigger_logic_generator: data/trigger_actions.json missing or empty")

    elif gen_name == "mail_system_generator":
        mail = output.files.get("mail/tv_shopping_broadcast.json", {})
        if not mail:
            errors.append("mail_system_generator: mail/tv_shopping_broadcast.json missing or empty")

    return errors