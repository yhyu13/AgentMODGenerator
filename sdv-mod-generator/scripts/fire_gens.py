"""Fire real generation requests via the Discord-bot path and poll status.

Mimics what app/discord/bot.py does for a user message: register a
notification target (so the running server's notifier DMs the zip), then
run the pipeline. Polls Redis until every request is done/failed and prints
a status table. The DM side is handled by the live uvicorn server's
CompletionNotifier (it polls Redis independently).

Usage:
    ./.a019-r3-verify/Scripts/python.exe scripts/fire_gens.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

from app.config import _dotenv_path  # noqa: E402
if _dotenv_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_dotenv_path, override=True)

# The Discord user to DM (from the live server logs: yhyu13's user id).
DISCORD_USER_ID = "1074287117804515398"

PROMPTS: list[tuple[str, str]] = [
    ("shop", "make a TV shopping channel that sells rare seeds every Sunday"),
    ("npc", "give the NPC Linus a new daily schedule and new dialogue lines"),
    ("weapon", "add a legendary golden sword weapon with high damage"),
]


async def main() -> int:
    from orchestrator.pipeline import run_pipeline_background
    from storage.redis import get_status, set_notification_target, set_status

    tasks: dict[str, asyncio.Task] = {}
    labels: dict[str, str] = {}
    for label, prompt in PROMPTS:
        request_id = f"gen_{label}"
        labels[request_id] = label
        await set_status(request_id, "pending")
        await set_notification_target(request_id, user_id=DISCORD_USER_ID, channel_id=0)
        tasks[request_id] = run_pipeline_background(request_id, DISCORD_USER_ID, prompt)
        print(f"FIRED {request_id} ({label}): {prompt!r}")

    print("\nPolling status every 5s...\n")
    done: set[str] = set()
    while len(done) < len(tasks):
        for rid in tasks:
            if rid in done:
                continue
            status = await get_status(rid)
            if status and (status.startswith("done:") or status == "failed"):
                done.add(rid)
                print(f"  {rid} [{labels[rid]}] -> {status}")
        if len(done) < len(tasks):
            await asyncio.sleep(5)

    # Await the pipeline tasks so any exception surfaces.
    for rid, task in tasks.items():
        await task

    print("\nAll requests finished. The live server's notifier should now DM each zip.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
