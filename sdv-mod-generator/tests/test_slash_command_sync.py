"""Tests for app/discord/bot._sync_command_tree.

Pins the slash-command registration behavior added when fixing the
"commands defined but never uploaded to Discord" gap: the
``@_bot.tree.command(...)`` handlers in ``start_bot`` are inert until
``tree.sync()`` runs, so ``on_ready`` now calls ``_sync_command_tree``.
These tests exercise the two sync modes (per-guild vs global) and the
error-propagation contract without a live Discord gateway.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.discord.bot import _sync_command_tree


class _FakeConfig:
    def __init__(self, guild_id: str) -> None:
        self.discord_sync_guild_id = guild_id


class _FakeTree:
    def __init__(self) -> None:
        self.sync = AsyncMock()
        self.copy_global_to = MagicMock()


@pytest.mark.asyncio
async def test_guild_sync_when_guild_id_set() -> None:
    tree = _FakeTree()
    bot = MagicMock()
    bot.tree = tree
    tree.sync.return_value = [MagicMock(), MagicMock(), MagicMock()]

    synced = await _sync_command_tree(bot, _FakeConfig("123456789"))

    # Global commands must be copied down into guild scope before the
    # per-guild sync, otherwise tree.sync(guild=...) pushes zero commands
    # (the command_count:0 bug — commands are registered globally, not
    # per-guild).
    tree.copy_global_to.assert_called_once()
    copy_kwargs = tree.copy_global_to.call_args.kwargs
    assert copy_kwargs["guild"].id == 123456789

    # Per-guild sync was called with a discord.Object for that guild id.
    tree.sync.assert_awaited_once()
    kwargs = tree.sync.await_args.kwargs
    assert "guild" in kwargs
    assert kwargs["guild"].id == 123456789
    assert len(synced) == 3


@pytest.mark.asyncio
async def test_global_sync_when_no_guild_id() -> None:
    tree = _FakeTree()
    bot = MagicMock()
    bot.tree = tree
    tree.sync.return_value = [MagicMock()]

    synced = await _sync_command_tree(bot, _FakeConfig(""))

    # Global sync: no guild kwarg.
    tree.sync.assert_awaited_once()
    assert tree.sync.await_args.kwargs == {}
    assert len(synced) == 1


@pytest.mark.asyncio
async def test_global_sync_when_config_missing_attr() -> None:
    # Older Config objects (or mocks) without the field must fall back to
    # global sync rather than crash on attribute access.
    tree = _FakeTree()
    bot = MagicMock()
    bot.tree = tree
    tree.sync.return_value = []

    synced = await _sync_command_tree(bot, object())

    tree.sync.assert_awaited_once()
    assert tree.sync.await_args.kwargs == {}
    assert synced == []


@pytest.mark.asyncio
async def test_sync_failure_propagates() -> None:
    # A failed sync must raise (so on_ready can log it), not silently
    # leave commands unregistered.
    tree = _FakeTree()
    bot = MagicMock()
    bot.tree = tree
    tree.sync.side_effect = RuntimeError("guild not found")

    with pytest.raises(RuntimeError):
        await _sync_command_tree(bot, _FakeConfig("123456789"))
