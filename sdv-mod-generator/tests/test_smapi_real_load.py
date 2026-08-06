"""Windows-compatible real SMAPI load gate.

``scripts/sdv_smoke_test.sh`` is POSIX-only, so this pytest module is
the gate that actually launches ``StardewModdingAPI.exe`` against a real
Stardew Valley install and fails if SMAPI or Content Patcher reports a
mod load problem.

Environment variables:
  SDV_INSTALL_PATH    Path to the Stardew Valley install dir
                      (default: ``D:\\SteamLibrary\\steamapps\\common\\Stardew Valley``).
  SDV_SMOKE_TEST_MOD  Optional path to a generated mod zip or extracted
                      mod dir to copy into the game ``Mods/`` folder
                      (removed afterwards). When unset the currently
                      installed ``Mods/`` folder is validated as a
                      smoke check.
"""
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from tests.smapi_load_parser import find_smapi_failures

DEFAULT_SDV_INSTALL = Path(r"D:\SteamLibrary\steamapps\common\Stardew Valley")
SMAPI_EXE = "StardewModdingAPI.exe"
SMAPI_LOG_TIMEOUT_SECONDS = 90

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="real SMAPI load test launches StardewModdingAPI.exe (Windows only)",
)


def _sdv_install() -> Path:
    return Path(os.environ.get("SDV_INSTALL_PATH", str(DEFAULT_SDV_INSTALL)))


def _candidate_logs(sdv_install: Path) -> list[Path]:
    appdata_log = (
        Path(os.environ.get("APPDATA", "")) / "StardewValley" / "ErrorLogs" / "SMAPI-latest.txt"
    )
    return [
        appdata_log,
        sdv_install / "SMAPI-latest.txt",
        sdv_install / "smapi-internal" / "SMAPI-latest.txt",
    ]


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _copy_test_mod(source: Path, dest_dir: Path) -> None:
    """Copy a zip or an extracted mod directory into ``dest_dir``."""
    if source.is_dir():
        shutil.copytree(source, dest_dir, dirs_exist_ok=True)
    else:
        with zipfile.ZipFile(source) as zf:
            zf.extractall(dest_dir)


def _launch_smapi(sdv_install: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [str(sdv_install / SMAPI_EXE)],
        cwd=str(sdv_install),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_smapi_log(
    proc: subprocess.Popen,
    sdv_install: Path,
    timeout: int = SMAPI_LOG_TIMEOUT_SECONDS,
    settle_seconds: int = 4,
) -> Path | None:
    """Poll for the SMAPI log, returning it once mods have finished loading.

    Content Patcher logs its patch warnings a few seconds *after* SMAPI
    reports ``Mods loaded and ready!``, so we also require the log to
    stop growing for ``settle_seconds`` (or a failure line to appear)
    before returning. Returns ``None`` if no log appeared before the
    timeout or the process exited without writing one.
    """
    candidates = _candidate_logs(sdv_install)
    deadline = time.monotonic() + timeout
    seen: Path | None = None
    last_size = -1
    last_change = 0.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        found = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
        if found is not None:
            seen = found
            size = found.stat().st_size
            text = _read_log(found)
            if find_smapi_failures(text):
                return found
            if size != last_size:
                last_size = size
                last_change = time.monotonic()
            elif "mods loaded and ready" in text.lower():
                if time.monotonic() - last_change >= settle_seconds:
                    return found
        time.sleep(1)
    return seen


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Kill the SMAPI/game process tree (Windows) if still running."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )


def test_real_smapi_load() -> None:
    """Launch the installed SMAPI and assert every loaded mod is clean."""
    sdv_install = _sdv_install()
    if not sdv_install.is_dir():
        pytest.skip(f"SDV_INSTALL_PATH not found: {sdv_install}")

    mods_dir = sdv_install / "Mods"
    if not mods_dir.is_dir():
        pytest.skip(f"no Mods folder at {mods_dir}; is SMAPI installed?")
    if not (sdv_install / SMAPI_EXE).is_file():
        pytest.skip(f"{SMAPI_EXE} not found in {sdv_install}; is SMAPI installed?")

    # Delete stale logs so only this run is inspected.
    for log in _candidate_logs(sdv_install):
        log.unlink(missing_ok=True)

    temp_mod_dir: Path | None = None
    proc = None
    try:
        test_mod = os.environ.get("SDV_SMOKE_TEST_MOD")
        if test_mod:
            source = Path(test_mod)
            if not source.exists():
                pytest.fail(f"SDV_SMOKE_TEST_MOD does not exist: {source}")
            temp_mod_dir = Path(tempfile.mkdtemp(dir=str(mods_dir), prefix="AgentModSmokeTest_"))
            _copy_test_mod(source, temp_mod_dir)

        proc = _launch_smapi(sdv_install)
        log_path = _wait_for_smapi_log(proc, sdv_install)

        if log_path is None:
            pytest.fail(
                f"SMAPI did not write a log within {SMAPI_LOG_TIMEOUT_SECONDS}s "
                f"(process exited, rc={proc.poll()}). Check {sdv_install}."
            )

        log_text = _read_log(log_path)
        lowered = log_text.lower()
        assert "mods loaded and ready" in lowered or "smapi 4.5.2" in lowered, (
            "SMAPI did not start successfully: the log does not contain "
            f"'Mods loaded and ready!' ({log_path})"
        )

        failures = find_smapi_failures(log_text)
        assert not failures, (
            "SMAPI/Content Patcher reported mod load problems:\n" + "\n".join(failures)
        )
    finally:
        if proc is not None:
            _terminate_process_tree(proc)
        if temp_mod_dir is not None:
            shutil.rmtree(temp_mod_dir, ignore_errors=True)
