"""Full Discord-bot return-zip flow, end to end.

Mirrors exactly what `app/discord/bot.py`'s `/generate` command does:
1. redis_set_status(request_id, "pending")
2. set_notification_target(request_id, user_id, channel_id)
3. run_pipeline_background(request_id, user_id, prompt)
4. await the background task
5. assert Redis status became "done:<zip_key>"
6. run CompletionNotifier._tick against a fake bot and assert the zip was
   DMed with a bare-basename filename.

This is NOT a pytest module — run directly with the venv python after
docker compose is up (Redis + Postgres):
    ./.a019-r3-verify/Scripts/python.exe scripts/e2e_discord_flow.py "<prompt>"
"""
from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

from app.config import _dotenv_path  # noqa: E402
if _dotenv_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_dotenv_path, override=True)


async def main() -> int:
    import os
    from app.discord.notifier import CompletionNotifier
    from orchestrator.pipeline import run_pipeline_background
    from storage.redis import (
        delete_notification_target,
        get_status,
        set_notification_target,
        set_status,
    )

    prompt = sys.argv[1] if len(sys.argv) > 1 else \
        "make a TV shopping channel that sells rare seeds every Sunday"
    request_id = "e2e_discord"
    user_id = "123456789012345678"  # numeric Discord snowflake, as bot.py uses

    # 1+2. Exactly what bot.py does before launching the pipeline.
    await set_status(request_id, "pending")
    await set_notification_target(request_id, user_id=user_id, channel_id=12345)

    # 3+4. Background pipeline, awaited to completion.
    task = run_pipeline_background(request_id, user_id, prompt)
    await task

    # 5. Status must be done:<zip_key>.
    status = await get_status(request_id)
    print(f"REDIS STATUS: {status}")
    if not status or not status.startswith("done:"):
        await delete_notification_target(request_id)
        print("FAIL: status is not done:<zip_key>")
        return 1
    zip_key = status.split(":", 1)[1]
    print(f"ZIP KEY: {zip_key}")

    # 6. Notifier tick must DM the zip with a bare-basename filename.
    bot = MagicMock()
    user = MagicMock()
    user.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=user)

    notifier = CompletionNotifier(bot)
    await notifier._tick()

    if not user.send.await_count:
        await delete_notification_target(request_id)
        print("FAIL: notifier did not DM the user")
        return 1

    call_args = user.send.await_args
    content = call_args.args[0]
    file_arg = call_args.kwargs.get("file") or call_args.args[1]
    print(f"DM CONTENT: {content}")
    print(f"DM FILE: {file_arg.filename if file_arg else None}")
    if file_arg is None:
        await delete_notification_target(request_id)
        print("FAIL: no zip attached to DM")
        return 1
    if file_arg.filename != Path(zip_key).name:
        await delete_notification_target(request_id)
        print(f"FAIL: filename {file_arg.filename!r} != basename {Path(zip_key).name!r}")
        return 1

    # Verify the DM'd zip is actually a valid zip with manifest.json.
    zip_path = Path(os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")) / zip_key
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "manifest.json" in names, names
    print(f"ZIP VALID: {len(names)} files, manifest.json present")

    await delete_notification_target(request_id)
    print("PASS: prompt -> pipeline -> zip -> Discord DM with correct filename")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
