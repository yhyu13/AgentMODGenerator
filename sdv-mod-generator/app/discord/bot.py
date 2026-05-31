"""Discord bot client."""

import structlog
import discord
from discord import app_commands

from app.config import get_config
from app.discord.commands import setup_commands

logger = structlog.get_logger()

_intents = discord.Intents.default()
_intents.message_content = True

_client: discord.Client | None = None
_tree: app_commands.CommandTree | None = None


async def start_bot() -> None:
    global _client, _tree

    config = get_config()
    token = config.discord_bot_token

    if not token:
        logger.warning("discord.bot.start.skipped", reason="no_token")
        return

    _client = discord.Client(intents=_intents)
    _tree = app_commands.CommandTree(_client)

    setup_commands(_tree)

    await _client.start(token)


async def get_bot() -> discord.Client | None:
    return _client


def get_tree() -> app_commands.CommandTree | None:
    return _tree