"""Discord bot client."""

import asyncio
from typing import Any
import structlog
import discord
from discord.ext import commands

from app.config import get_config
from app.discord.commands import setup_commands

logger = structlog.get_logger()

_intents = discord.Intents.default()
_intents.messages = True
_intents.message_content = True

_bot: commands.Bot | None = None


def _patch_http_for_proxy() -> None:
    """Patch discord.py HTTP client to use proxy from environment."""
    import aiohttp
    from aiohttp_socks import ProxyConnector
    from discord import http
    import os

    _proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
    if not _proxy_url:
        logger.warning("discord.bot.proxy.not_configured", message="ALL_PROXY not set, skipping proxy patching")
        return

    async def patched_static_login(self, token: str):
        proxy_connector = ProxyConnector.from_url(_proxy_url)

        self._HTTPClient__session = aiohttp.ClientSession(
            connector=proxy_connector,
            trace_configs=None,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        self._global_over = asyncio.Event()
        self._global_over.set()

        old_token = self.token
        self.token = token

        try:
            data = await self.request(http.Route("GET", "/users/@me"))
        except discord.errors.HTTPException as exc:
            self.token = old_token
            await self._HTTPClient__session.close()
            if exc.status == 401:
                raise discord.errors.LoginFailure("Improper token has been passed.") from exc
            raise
        finally:
            if not self._HTTPClient__session.closed:
                await self._HTTPClient__session.close()

        return data

    http.HTTPClient.static_login = patched_static_login

    async def patched_ws_connect(self, url: str, *, compress: int = 0):
        try:
            timeout: Any = aiohttp.ClientWSTimeout(ws_close=30.0)
        except (AttributeError, TypeError):
            timeout = 30.0

        kwargs = {
            'max_msg_size': 0,
            'timeout': timeout,
            'autoclose': False,
            'headers': {
                'User-Agent': self.user_agent,
            },
            'compress': compress,
        }

        return await self._HTTPClient__session.ws_connect(url, **kwargs)

    http.HTTPClient.ws_connect = patched_ws_connect


async def start_bot() -> None:
    global _bot

    config = get_config()
    token = config.discord_bot_token

    if not token:
        logger.warning("discord.bot.start.skipped", reason="no_token")
        return

    _patch_http_for_proxy()

    _bot = commands.Bot(command_prefix="!", intents=_intents)

    @_bot.event
    async def on_message(message: discord.Message) -> None:
        logger.info("discord.message.received", author=str(message.author), content=message.content)
        if message.author.bot:
            return
        content = message.content.lower().strip()
        if content in ("hi", "hello", "hey", "你好", "嗨"):
            await message.channel.send(
                "Hello! I'm Agent Mod 0x01. Use `/generate <prompt>` to create a Stardew Valley mod."
            )

    setup_commands(_bot)

    @_bot.event
    async def on_ready() -> None:
        logger.info("discord.bot.ready", user=str(_bot.user))

    logger.info("discord.bot.starting", app_id=config.discord_app_id or "global")
    await asyncio.wait_for(_bot.start(token), timeout=60)


def get_bot() -> commands.Bot | None:
    return _bot