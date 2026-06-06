"""ZIP packager — real implementation using zipfile."""
import json
import os
import re
import structlog
import zipfile
from pathlib import Path
from typing import Any

logger = structlog.get_logger()

_LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")

_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_request_id(request_id: str) -> None:
    if not _REQUEST_ID_PATTERN.match(request_id):
        raise ValueError(f"Invalid request_id format: {request_id!r}")


def _validate_asset_path(asset_path: str) -> bool:
    asset_path = asset_path.strip()
    if "\x00" in asset_path:
        return False
    normalized = _normalize_path(asset_path)
    if normalized.startswith(".."):
        return False
    return True


def package(request_id: str, files: dict[str, dict], assets: list[str]) -> str:
    """Package files + assets into a Content Patcher zip.

    Writes zip to LOCAL_OUTPUT_DIR/mods/{request_id}/{request_id}.zip
    Returns the zip key (filename only).

    Fails loudly on any asset issue — missing files, absolute paths, rejected
    paths, write errors. The pipeline catches these and marks the request
    failed with the path in the error. This replaces the previous behavior
    of silently skipping bad assets (the "swallowed errors" pattern from
    AGENTS.md root-causes table).
    """
    logger.info("packager.run", request_id=request_id, file_count=len(files), asset_count=len(assets))

    _validate_request_id(request_id)

    mod_dir = Path(_LOCAL_OUTPUT_DIR) / "mods" / request_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    zip_path = mod_dir / f"{request_id}.zip"

    written_paths: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, content in files.items():
            normalized = _normalize_path(file_path)
            if isinstance(content, (dict, list)):
                zf.writestr(normalized, json.dumps(content, indent=2, ensure_ascii=False))
            elif isinstance(content, str):
                zf.writestr(normalized, content)
            elif isinstance(content, bytes):
                zf.writestr(normalized, content)
            else:
                zf.writestr(normalized, str(content))
            written_paths.add(normalized)

        for asset_path in assets:
            if not asset_path:
                raise ValueError(f"packager: empty asset path in request {request_id}")
            if not _validate_asset_path(asset_path):
                raise ValueError(f"packager: asset path rejected (path traversal or null byte): {asset_path!r}")
            if os.path.isabs(asset_path):
                raise ValueError(f"packager: asset path must be relative, got absolute: {asset_path!r}")
            normalized = _normalize_path(asset_path)
            if not os.path.exists(asset_path):
                raise FileNotFoundError(
                    f"packager: asset not found on disk: {asset_path!r} "
                    f"(generator should use add_file for in-memory content, "
                    f"not add_asset which expects a real file path)"
                )
            if normalized in written_paths:
                # already written from in-memory `files`; don't double-write
                logger.info("packager.asset_already_in_files", path=asset_path)
                continue
            try:
                zf.write(asset_path, normalized)
                written_paths.add(normalized)
            except Exception as exc:
                raise OSError(f"packager: failed to write asset {asset_path!r}: {exc}") from exc

    zip_key = f"mods/{request_id}/{request_id}.zip"
    logger.info("packager.done", request_id=request_id, zip_key=zip_key, zip_size=zip_path.stat().st_size)
    return zip_key


def _normalize_path(path: str) -> str:
    if "\x00" in path:
        raise ValueError(f"Null byte in path: {path!r}")
    path = path.strip().replace("\\", "/")
    while path.startswith("/"):
        path = path[1:]
    if ".." in path:
        raise ValueError(f"Path traversal attempt detected: {path}")
    return path


_ZIP_KEY_PATTERN = re.compile(r"^mods/[a-zA-Z0-9_-]{1,64}/[a-zA-Z0-9_-]{1,64}\.zip$")


def _validate_zip_key(zip_key: str) -> None:
    if "\x00" in zip_key:
        raise ValueError(f"Null byte in zip_key: {zip_key!r}")
    if ".." in zip_key:
        raise ValueError(f"Invalid zip_key: {zip_key}")
    if not _ZIP_KEY_PATTERN.match(zip_key):
        raise ValueError(f"Invalid zip_key format: {zip_key!r}")


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