"""End-to-end test mimicking Discord /generate command flow.

This is an integration test that requires a running API server.
Run manually with: python tests/test_discord_flow.py
"""
import asyncio
import aiohttp
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(reason="Integration test: requires running API server")

API_BASE = "http://localhost:8000"
PROMPT = "做一个电视购物频道"
POLL_INTERVAL = 2
MAX_POLLS = 120


async def submit_generation(user_id: str, prompt: str) -> str | None:
    payload = {"user_id": user_id, "prompt": prompt}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/v1/mods/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                print(f"  Submit failed: {resp.status}")
                return None
            data = await resp.json()
            return data.get("request_id")


async def poll_until_done(request_id: str) -> tuple[str, str | None]:
    async with aiohttp.ClientSession() as session:
        for i in range(MAX_POLLS):
            try:
                async with session.get(
                    f"{API_BASE}/v1/mods/{request_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 404:
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    status = data.get("status", "pending")
                    print(f"  Poll [{i+1}]: {status}")
                    if status in ("done", "failed"):
                        zip_key = data.get("zip_url", "")
                        return status, zip_key
            except Exception as exc:
                print(f"  Poll error: {exc}")
            await asyncio.sleep(POLL_INTERVAL)
    return "failed", None


def verify_zip(zip_path: Path) -> bool:
    if not zip_path.exists():
        print(f"  ZIP not found: {zip_path}")
        return False

    print(f"  ZIP: {zip_path.stat().st_size} bytes")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            files_list = ", ".join(names[:8])
            print(f"  Files ({len(names)}): {files_list}")

            if "manifest.json" not in names:
                print("  Missing manifest.json")
                return False

            import json
            m = json.loads(zf.read("manifest.json"))
            print(f"  manifest: {m.get('UniqueID')} / {m.get('Name')}")
            return True
    except Exception as exc:
        print(f"  ZIP error: {exc}")
        return False


async def main():
    print(f"[Discord Flow Test] Prompt: {PROMPT}\n")

    # SMAPI validator import — try package import first, fall back to direct
    # file load so the script can run via `python tests/test_discord_flow.py`
    try:
        from tests.smapi_validate import validate_zip_contents
    except ModuleNotFoundError:
        import importlib.util
        _smapi_path = Path(__file__).parent / "smapi_validate.py"
        _spec = importlib.util.spec_from_file_location("smapi_validate", _smapi_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        validate_zip_contents = _mod.validate_zip_contents

    # Submit
    print("[1/3] Submitting...")
    request_id = await submit_generation("test_user", PROMPT)
    if not request_id:
        print("FAILED: Could not submit")
        sys.exit(1)
    print(f"  request_id: {request_id}\n")

    # Poll
    print("[2/3] Polling...")
    status, zip_key = await poll_until_done(request_id)
    print(f"  Result: {status} / {zip_key}\n")

    if status != "done" or not zip_key:
        print(f"FAILED: {status}")
        sys.exit(1)

    # Verify
    print("[3/3] Verifying ZIP...")
    local_dir = Path("/tmp/sdv-mod-generator/outputs")
    ok = verify_zip(local_dir / zip_key)

    # SMAPI load-time check (P4.6 task 2)
    print("\n[3b/3] SMAPI manifest validation...")
    smapi_errors = validate_zip_contents(local_dir / zip_key)
    if smapi_errors:
        print(f"  SMAPI errors: {smapi_errors}")
        print("FAILED: SMAPI validation")
        sys.exit(1)
    print("  SMAPI manifest validation PASSED")

    print()
    if ok:
        print("PASSED")
    else:
        print("FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
