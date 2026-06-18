"""Background watcher: DM the user when their mod request finishes.

Polls Redis for registered notification targets, checks their pipeline status,
and DMs the zip on success / a status hint on failure. Self-cleans notification
targets after firing. Never lets an exception kill the loop.
"""
import asyncio
import os
from pathlib import Path

import discord
import structlog

from storage.redis import (
    delete_notification_target,
    get_status,
    list_pending_notifications,
)

logger = structlog.get_logger()

_POLL_INTERVAL_SECONDS = 3.0
_LOCAL_OUTPUT_DIR = Path(os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs"))


class CompletionNotifier:
    """Long-lived background task that pushes Discord DMs when requests finish."""

    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="discord-completion-notifier")
        logger.info("discord.notifier.started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("discord.notifier.stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                # Never let one bad request kill the watcher.
                logger.error(
                    "discord.notifier.tick.error",
                    error=str(exc), error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        for request_id, target in await list_pending_notifications():
            status = await get_status(request_id)
            if not status:
                continue
            if status == "failed":
                await self._fire_failure(request_id, target)
                await delete_notification_target(request_id)
            elif status.startswith("done:"):
                zip_key = status.split(":", 1)[1]
                await self._fire_success(request_id, target, zip_key)
                await delete_notification_target(request_id)

    async def _fire_success(self, request_id: str, target: dict, zip_key: str) -> None:
        user = await self._safe_fetch_user(target["user_id"])
        if not user:
            return
        zip_path = _LOCAL_OUTPUT_DIR / zip_key
        try:
            content = f"✅ Mod ready! Request `{request_id}`"
            if zip_path.exists():
                await user.send(content, file=discord.File(zip_path, filename=zip_key))
            else:
                await user.send(f"{content}\n(zip not on disk: `{zip_key}` — check `/status`)")
            logger.info(
                "discord.notifier.sent.success",
                request_id=request_id, user_id=target["user_id"],
            )
        except discord.Forbidden:
            logger.warning(
                "discord.notifier.dm_forbidden",
                request_id=request_id, user_id=target["user_id"],
            )
        except Exception as exc:
            logger.error(
                "discord.notifier.send.error",
                request_id=request_id, error=str(exc),
            )

    async def _fire_failure(self, request_id: str, target: dict) -> None:
        user = await self._safe_fetch_user(target["user_id"])
        if not user:
            return
        try:
            await user.send(
                f"❌ Mod generation failed for `{request_id}`. "
                f"Use `/status {request_id}` for details."
            )
            logger.info(
                "discord.notifier.sent.failure",
                request_id=request_id, user_id=target["user_id"],
            )
        except discord.Forbidden:
            pass

    async def _safe_fetch_user(self, user_id: str) -> discord.User | None:
        try:
            return await self._bot.fetch_user(int(user_id))
        except (discord.NotFound, discord.HTTPException) as exc:
            logger.warning(
                "discord.notifier.user_lookup.failed",
                user_id=user_id, error=str(exc),
            )
            return None
