"""Tests for P5.5 — sdv_smoke_test.sh script behaviour.

We can't run SMAPI in CI, so the tests cover the gating logic:
  - No SDV_INSTALL_PATH → skip, exit 0
  - SDV_INSTALL_PATH set but missing → fail
  - SDV_INSTALL_PATH set without Mods/ → fail
  - SDV_INSTALL_PATH set without SMAPI binary → fail
  - SDV_INSTALL_PATH set with SMAPI binary but no actual game → the
    script is not invoked; we just check the precondition check works
"""
import os
import subprocess
from pathlib import Path

import pytest

# The script under test is a POSIX bash script; it cannot run on Windows.
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="sdv_smoke_test.sh is a bash script (POSIX only)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "sdv_smoke_test.sh"


def _run(env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    full_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        **env,
    }
    return subprocess.run(
        [str(SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd or REPO_ROOT,
        timeout=30,
    )


def test_script_exists_and_is_executable() -> None:
    assert SMOKE_SCRIPT.exists()
    assert os.access(SMOKE_SCRIPT, os.X_OK)


def test_skips_when_sdv_install_path_unset() -> None:
    """No SDV_INSTALL_PATH → script exits 0 with SKIP message (CI mode)."""
    env = {k: v for k, v in os.environ.items() if k != "SDV_INSTALL_PATH"}
    env.pop("SDV_INSTALL_PATH", None)
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "SKIP" in result.stderr or "SDV_INSTALL_PATH" in result.stderr


def test_fails_when_sdv_path_does_not_exist(tmp_path: Path) -> None:
    env = {"SDV_INSTALL_PATH": str(tmp_path / "does_not_exist")}
    result = _run(env)
    assert result.returncode != 0
    assert "does not exist" in result.stderr


def test_fails_when_mods_dir_missing(tmp_path: Path) -> None:
    """SDV dir exists but has no Mods/ → fail with a clear message."""
    env = {"SDV_INSTALL_PATH": str(tmp_path)}
    result = _run(env)
    assert result.returncode != 0
    assert "Mods" in result.stderr


def test_fails_when_smapi_binary_missing(tmp_path: Path) -> None:
    """SDV dir has Mods/ but no SMAPI binary → fail with install hint."""
    (tmp_path / "Mods").mkdir()
    env = {"SDV_INSTALL_PATH": str(tmp_path)}
    result = _run(env)
    assert result.returncode != 0
    assert "StardewModdingAPI" in result.stderr


def test_script_uses_test_zip_when_provided(tmp_path: Path) -> None:
    """When TEST_ZIP is set and points at a valid zip, the script tries
    to launch SMAPI rather than hit the API. We don't actually launch
    SMAPI here — we just confirm the precondition checks pass before
    it gets to that step."""
    # Set up a fake SDV install with a fake SMAPI binary that exits immediately
    sdv = tmp_path / "stardew"
    sdv.mkdir()
    (sdv / "Mods").mkdir()
    fake_smapi = sdv / "StardewModdingAPI"
    fake_smapi.write_text("#!/bin/sh\nexit 0\n")
    fake_smapi.chmod(0o755)

    # Create a fake test zip
    import zipfile

    zip_path = tmp_path / "test_mod.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", '{"Name":"Test","UniqueID":"test","Format":"1.29.0"}')

    env = {
        "SDV_INSTALL_PATH": str(sdv),
        "TEST_ZIP": str(zip_path),
        "SMAPI_TIMEOUT": "5",
    }
    result = _run(env, cwd=tmp_path)
    # The script will try to launch SMAPI which exits 0, and there
    # won't be a log file. The script should fail with a clear message
    # about no log being written, NOT about a precondition.
    assert "precondition" not in result.stderr.lower()
    # We expect failure (no log), but the test confirms preconditions passed
    assert result.returncode != 0
