"""ZIP packager — real implementation using zipfile."""
import json
import os
import structlog
import zipfile
from pathlib import Path
from typing import Any

logger = structlog.get_logger()

_LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")


def package(request_id: str, files: dict[str, dict], assets: list[str]) -> str:
    """Package files + assets into a Content Patcher zip.

    Writes zip to LOCAL_OUTPUT_DIR/mods/{request_id}/{request_id}.zip
    Returns the zip key (filename only).
    """
    logger.info("packager.run", request_id=request_id, file_count=len(files), asset_count=len(assets))

    mod_dir = Path(_LOCAL_OUTPUT_DIR) / "mods" / request_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    zip_path = mod_dir / f"{request_id}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, content in files.items():
            normalized = _normalize_path(file_path)
            if isinstance(content, dict):
                zf.writestr(normalized, json.dumps(content, indent=2, ensure_ascii=False))
            elif isinstance(content, str):
                zf.writestr(normalized, content)
            else:
                zf.writestr(normalized, str(content))

        for asset_path in assets:
            if not asset_path:
                continue
            normalized = _normalize_path(asset_path)
            if os.path.isabs(asset_path) and os.path.exists(asset_path):
                zf.write(asset_path, normalized)
            elif os.path.exists(asset_path):
                zf.write(asset_path, normalized)

    zip_key = f"mods/{request_id}/{request_id}.zip"
    logger.info("packager.done", request_id=request_id, zip_key=zip_key, zip_size=zip_path.stat().st_size)
    return zip_key


def _normalize_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    while path.startswith("/"):
        path = path[1:]
    if ".." in path:
        raise ValueError(f"Path traversal attempt detected: {path}")
    return path


def _validate_zip_key(zip_key: str) -> None:
    if ".." in zip_key:
        raise ValueError(f"Invalid zip_key: {zip_key}")


def read_zip(zip_key: str) -> dict[str, Any]:
    _validate_zip_key(zip_key)
    zip_path = Path(_LOCAL_OUTPUT_DIR) / zip_key
    if not zip_path.exists():
        return {}
    result: dict[str, Any] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            result[name] = zf.read(name).decode("utf-8", errors="replace")
    return result