"""Tests for app/discord/notifier.CompletionNotifier.

Pins the DM-on-completion behavior added in 8af9d90 (PR 1) which
had no automated test coverage — only the manual smoke check from
the original port. The 267/267 test count after PRs 1+2 was
misleading: it confirmed no regressions in pre-existing tests, but
did not pin the notifier's complex asyncio + Redis + Discord
interactions.

The notifier is structured as six testable pieces:

1. ``_safe_fetch_user`` — wraps ``bot.fetch_user`` and converts
   NotFound / HTTPException to a ``None`` return.
2. ``_fire_success`` — sends a DM with the zip as a Discord
   attachment. If the zip is on disk: attach. If missing: send
   a text-only fallback DM with the path the user should check.
3. ``_fire_failure`` — sends a DM with the failure message.
4. ``_tick`` — pulls pending notifications, looks up status,
   fires success or failure, and DELETEs the target after firing
   so the watcher never re-fires the same notification.
5. ``_run`` — the polling loop; one bad tick MUST NOT kill the
   watcher (it catches Exception and logs, then sleeps).
6. ``start`` / ``stop`` — task lifecycle. ``stop`` cancels the
   task and awaits the cancellation.

AsyncMock fixtures: the notifier calls three storage.redis
functions (list_pending_notifications, get_status,
delete_notification_target) and one discord.Client method
(fetch_user). All four are AsyncMock-able.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from app.discord import notifier as notifier_mod
from app.discord.notifier import CompletionNotifier


@pytest.fixture
def mock_bot() -> MagicMock:
    """A MagicMock pretending to be a discord.Client.

    The notifier only calls ``bot.fetch_user(int(user_id))`` which
    returns a ``discord.User`` that we further mock. All other
    bot methods are auto-MagicMock'd (returning MagicMock), which
    is fine because the notifier doesn't call them.
    """
    return MagicMock(spec=discord.Client)


@pytest.fixture
def mock_user() -> AsyncMock:
    """A mocked discord.User whose .send() is an AsyncMock.

    The notifier calls ``user.send(content, file=...)`` exactly
    once per fire_success / fire_failure. The mock records the
    calls so tests can assert on them.
    """
    user = MagicMock(spec=discord.User)
    user.send = AsyncMock()
    return user


@pytest.fixture
def mock_storage() -> dict[str, AsyncMock]:
    """AsyncMock for the three storage.redis functions the notifier uses.

    Returns a dict so each test can reset state with a one-liner and
    set per-test return values explicitly.
    """
    return {
        "list_pending_notifications": AsyncMock(return_value=[]),
        "get_status": AsyncMock(return_value=None),
        "delete_notification_target": AsyncMock(),
    }


@pytest.fixture
def notif(
    mock_bot: MagicMock, mock_storage: dict[str, AsyncMock]
) -> CompletionNotifier:
    """A notifier with storage mocked. Local-output-dir defaults to /tmp.

    Patches the storage.redis functions on the notifier's module
    namespace (NOT on storage.redis) because the notifier imported
    them by name at module-load time. Patching the names in the
    notifier's namespace is the right seam.
    """
    with patch.object(notifier_mod, "list_pending_notifications", mock_storage["list_pending_notifications"]), \
         patch.object(notifier_mod, "get_status", mock_storage["get_status"]), \
         patch.object(notifier_mod, "delete_notification_target", mock_storage["delete_notification_target"]):
        yield CompletionNotifier(mock_bot)


# ---------------------------------------------------------------------------
# _safe_fetch_user
# ---------------------------------------------------------------------------


class TestSafeFetchUser:
    """``_safe_fetch_user`` wraps bot.fetch_user with NotFound/HTTPException → None."""

    @pytest.mark.asyncio
    async def test_returns_user_on_success(
        self, notif: CompletionNotifier, mock_bot: MagicMock, mock_user: AsyncMock
    ) -> None:
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        result = await notif._safe_fetch_user("12345")
        assert result is mock_user
        mock_bot.fetch_user.assert_awaited_once_with(12345)

    @pytest.mark.asyncio
    async def test_returns_none_on_not_found(
        self, notif: CompletionNotifier, mock_bot: MagicMock
    ) -> None:
        mock_bot.fetch_user = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), MagicMock())
        )
        result = await notif._safe_fetch_user("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_exception(
        self, notif: CompletionNotifier, mock_bot: MagicMock
    ) -> None:
        # discord.HTTPException is the umbrella for 4xx/5xx responses.
        # The exception constructor requires a `message` arg.
        response = MagicMock()
        response.status = 503
        mock_bot.fetch_user = AsyncMock(
            side_effect=discord.HTTPException(response, "service unavailable")
        )
        result = await notif._safe_fetch_user("12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_value_error(
        self, notif: CompletionNotifier, mock_bot: MagicMock
    ) -> None:
        # int("not-an-int") raises ValueError, not NotFound. The
        # function should NOT swallow arbitrary errors; only the
        # documented NotFound/HTTPException. So this test pins the
        # "let other errors propagate" contract.
        mock_bot.fetch_user = AsyncMock(
            side_effect=ValueError("user_id must be int")
        )
        with pytest.raises(ValueError):
            await notif._safe_fetch_user("not-an-int")


# ---------------------------------------------------------------------------
# _fire_success
# ---------------------------------------------------------------------------


class TestFireSuccess:
    """``_fire_success`` DMs the user with the zip attached, or a fallback
    text DM if the zip is not on disk."""

    @pytest.mark.asyncio
    async def test_sends_zip_when_on_disk(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Create a real zip file on disk so the notifier can attach it.
        zip_key = "mod_abc123.zip"
        zip_path = tmp_path / zip_key
        zip_path.write_bytes(b"PK\x03\x04fake-zip-content")
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)

        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        target = {"user_id": "12345", "channel_id": "67890"}

        await notif._fire_success("req_abc", target, zip_key)

        # send was called exactly once, with the success message and a
        # discord.File pointing at the zip.
        mock_user.send.assert_awaited_once()
        call_args = mock_user.send.await_args
        content = call_args.args[0]
        assert "✅ Mod ready" in content
        assert "req_abc" in content
        file_arg = call_args.kwargs.get("file") or call_args.args[1]
        assert isinstance(file_arg, discord.File)
        # The discord.File's filename attribute carries the zip_key.
        assert file_arg.filename == zip_key

    @pytest.mark.asyncio
    async def test_sends_text_fallback_when_zip_missing(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No zip on disk; the notifier must still send a DM with a
        # helpful text message that names the missing zip_key.
        zip_key = "mod_missing.zip"
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)

        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        target = {"user_id": "12345", "channel_id": "67890"}

        await notif._fire_success("req_abc", target, zip_key)

        mock_user.send.assert_awaited_once()
        content = mock_user.send.await_args.args[0]
        assert "✅ Mod ready" in content
        assert "zip not on disk" in content
        assert zip_key in content
        # And no file argument.
        assert "file" not in mock_user.send.await_args.kwargs

    @pytest.mark.asyncio
    async def test_no_op_when_user_lookup_fails(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If the user has blocked the bot or doesn't exist, the
        # notifier must NOT raise — it should silently no-op so the
        # outer _tick loop continues to other targets.
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)
        mock_bot.fetch_user = AsyncMock(return_value=None)
        target = {"user_id": "12345", "channel_id": "67890"}

        await notif._fire_success("req_abc", target, "mod_x.zip")
        mock_user.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_discord_forbidden(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # discord.Forbidden (user blocked bot, DMs disabled) must be
        # caught and logged, not propagated to the watcher loop.
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)
        mock_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), MagicMock()))
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        target = {"user_id": "12345", "channel_id": "67890"}

        # Should not raise.
        await notif._fire_success("req_abc", target, "mod_x.zip")


# ---------------------------------------------------------------------------
# _fire_failure
# ---------------------------------------------------------------------------


class TestFireFailure:
    """``_fire_failure`` DMs the user with the failure message."""

    @pytest.mark.asyncio
    async def test_sends_failure_message(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
    ) -> None:
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        target = {"user_id": "12345", "channel_id": "67890"}

        await notif._fire_failure("req_abc", target)

        mock_user.send.assert_awaited_once()
        content = mock_user.send.await_args.args[0]
        assert "❌ Mod generation failed" in content
        assert "req_abc" in content
        assert "/status req_abc" in content

    @pytest.mark.asyncio
    async def test_no_op_when_user_lookup_fails(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
    ) -> None:
        mock_bot.fetch_user = AsyncMock(return_value=None)
        target = {"user_id": "12345", "channel_id": "67890"}

        await notif._fire_failure("req_abc", target)
        mock_user.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silently_swallows_forbidden(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
    ) -> None:
        # Unlike _fire_success (which logs a warning on Forbidden),
        # _fire_failure silently swallows Forbidden — the user
        # doesn't need a SECOND notification that they blocked the
        # bot, since they already got a "no" from the platform.
        mock_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), MagicMock()))
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        target = {"user_id": "12345", "channel_id": "67890"}

        # Should not raise.
        await notif._fire_failure("req_abc", target)


# ---------------------------------------------------------------------------
# _tick
# ---------------------------------------------------------------------------


class TestTick:
    """``_tick`` processes one batch of pending notifications."""

    @pytest.mark.asyncio
    async def test_no_targets_no_op(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # Empty list_pending_notifications result → no status lookups,
        # no fires, no deletes.
        mock_storage["list_pending_notifications"].return_value = []
        await notif._tick()
        mock_storage["get_status"].assert_not_awaited()
        mock_storage["delete_notification_target"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_running_status_skipped(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # A target whose status is "running" must NOT be fired or deleted.
        mock_storage["list_pending_notifications"].return_value = [
            ("req_1", {"user_id": "1", "channel_id": "1"}),
        ]
        mock_storage["get_status"].return_value = "running"
        await notif._tick()
        mock_storage["delete_notification_target"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_done_status_fires_success_and_deletes(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        mock_storage: dict[str, AsyncMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Create the zip on disk so the success path actually attaches it.
        zip_key = "mod_done.zip"
        (tmp_path / zip_key).write_bytes(b"PK")
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)

        mock_storage["list_pending_notifications"].return_value = [
            ("req_done", {"user_id": "1", "channel_id": "1"}),
        ]
        # Status format: "done:<zip_key>" — the notifier splits on
        # the first colon to extract the key.
        mock_storage["get_status"].return_value = f"done:{zip_key}"

        await notif._tick()

        mock_user.send.assert_awaited_once()
        mock_storage["delete_notification_target"].assert_awaited_once_with("req_done")

    @pytest.mark.asyncio
    async def test_failed_status_fires_failure_and_deletes(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)
        mock_storage["list_pending_notifications"].return_value = [
            ("req_fail", {"user_id": "1", "channel_id": "1"}),
        ]
        mock_storage["get_status"].return_value = "failed"

        await notif._tick()

        mock_user.send.assert_awaited_once()
        content = mock_user.send.await_args.args[0]
        assert "❌" in content
        mock_storage["delete_notification_target"].assert_awaited_once_with("req_fail")

    @pytest.mark.asyncio
    async def test_status_with_no_value_continues(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # get_status returning None → skip this target, don't delete.
        mock_storage["list_pending_notifications"].return_value = [
            ("req_x", {"user_id": "1", "channel_id": "1"}),
        ]
        mock_storage["get_status"].return_value = None
        await notif._tick()
        mock_storage["delete_notification_target"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_targets_processed(
        self,
        notif: CompletionNotifier,
        mock_bot: MagicMock,
        mock_user: AsyncMock,
        mock_storage: dict[str, AsyncMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two targets in one tick: one success, one failure, one
        # still running (skipped). The notifier must process all
        # three in a single tick.
        zip_key = "mod_a.zip"
        (tmp_path / zip_key).write_bytes(b"PK")
        monkeypatch.setattr(notifier_mod, "_LOCAL_OUTPUT_DIR", tmp_path)
        mock_bot.fetch_user = AsyncMock(return_value=mock_user)

        mock_storage["list_pending_notifications"].return_value = [
            ("req_done", {"user_id": "1", "channel_id": "1"}),
            ("req_fail", {"user_id": "1", "channel_id": "1"}),
            ("req_running", {"user_id": "1", "channel_id": "1"}),
        ]
        # Per-request status, dispatched by request_id.
        status_by_id = {
            "req_done": f"done:{zip_key}",
            "req_fail": "failed",
            "req_running": "running",
        }
        mock_storage["get_status"].side_effect = lambda rid: status_by_id.get(rid)

        await notif._tick()

        # Two DMs sent (success + failure), NOT three (running was skipped).
        assert mock_user.send.await_count == 2
        # Two deletes (success + failure), NOT three.
        assert mock_storage["delete_notification_target"].await_count == 2
        # The deletes went to the right request_ids.
        deleted_ids = {
            call.args[0] for call in mock_storage["delete_notification_target"].await_args_list
        }
        assert deleted_ids == {"req_done", "req_fail"}


# ---------------------------------------------------------------------------
# _run / start / stop
# ---------------------------------------------------------------------------


class TestRunLoop:
    """``_run`` is the long-lived polling loop. It must survive bad ticks."""

    async def test_run_survives_tick_exception(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # First tick raises, second tick returns empty list. The run
        # loop must NOT propagate the first tick's exception; it must
        # log and continue. We patch the poll interval to ~0 so the
        # second tick fires immediately after the first errors, then
        # stop the loop.
        import app.discord.notifier as nmod
        original_interval = nmod._POLL_INTERVAL_SECONDS
        nmod._POLL_INTERVAL_SECONDS = 0.01
        try:
            mock_storage["list_pending_notifications"].side_effect = [
                RuntimeError("redis SCAN failed"),
                [],  # second tick: no work
                [],  # third tick: still no work, but we stop after this
            ]

            async def stop_after_three_ticks() -> None:
                # Wait long enough for at least 2 ticks to fire, then stop.
                await asyncio.sleep(0.1)
                notif._stop.set()

            asyncio.create_task(stop_after_three_ticks())
            # The run loop should NOT raise — that's the whole point.
            await asyncio.wait_for(notif._run(), timeout=2.0)

            # Multiple ticks were attempted (proves the first error
            # didn't kill the loop).
            assert mock_storage["list_pending_notifications"].await_count >= 2
        finally:
            nmod._POLL_INTERVAL_SECONDS = original_interval

    @pytest.mark.asyncio
    async def test_start_then_stop(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # Lifecycle: start() creates a task; stop() cancels it cleanly.
        # Use a side_effect that returns [always] so the loop polls
        # indefinitely until stop() fires.
        mock_storage["list_pending_notifications"].return_value = []

        notif.start()
        assert notif._task is not None
        assert not notif._task.done()

        # Let the loop tick a few times.
        await asyncio.sleep(0.05)
        assert mock_storage["list_pending_notifications"].await_count >= 1

        await notif.stop()
        assert notif._task.done()

    @pytest.mark.asyncio
    async def test_start_idempotent(
        self,
        notif: CompletionNotifier,
        mock_storage: dict[str, AsyncMock],
    ) -> None:
        # Calling start() twice does NOT spawn a second task.
        mock_storage["list_pending_notifications"].return_value = []
        notif.start()
        first_task = notif._task
        notif.start()
        assert notif._task is first_task
        await notif.stop()


# ---------------------------------------------------------------------------
# Smoke: the module imports cleanly with a default-construction
# ---------------------------------------------------------------------------


def test_module_imports_cleanly() -> None:
    """A bare import test guards against the BadSignature-style
    regression where a wrong import name would break the whole
    module. If app/discord/notifier.py fails to import, this test
    fails to collect, which is the loudest possible signal.
    """
    assert hasattr(notifier_mod, "CompletionNotifier")
    assert hasattr(notifier_mod, "_POLL_INTERVAL_SECONDS")
    assert hasattr(notifier_mod, "_LOCAL_OUTPUT_DIR")
