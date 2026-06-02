"""Discord bot client."""

import asyncio
import uuid
import structlog
import discord
from discord import app_commands
from discord.ext import commands

from app.config import get_config
from orchestrator.pipeline import run_pipeline_background
from storage.redis import set_status as redis_set_status

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
        raise RuntimeError("Proxy not configured: set ALL_PROXY or all_proxy environment variable")

    original_static_login = http.HTTPClient.static_login

    async def patched_static_login(self, token: str):
        proxy_connector = ProxyConnector.from_url(_proxy_url)

        self._HTTPClient__session = aiohttp.ClientSession(
            connector=proxy_connector,
            ws_response_class=http.DiscordClientWebSocketResponse,
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
            if exc.status == 401:
                raise discord.errors.LoginFailure("Improper token has been passed.") from exc
            raise

        return data

    http.HTTPClient.static_login = patched_static_login

    original_ws_connect = http.HTTPClient.ws_connect

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


    try:
        _patch_http_for_proxy()
    except RuntimeError as exc:
        logger.warning("discord.bot.proxy.patch.skipped", reason=str(exc))

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

    @_bot.tree.command(
        name="generate",
        description="Generate a Stardew Valley mod from a text prompt",
    )
    @app_commands.describe(prompt="Describe the mod you want to create")
    async def generate_command(interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        request_id = f"req_{uuid.uuid4().hex[:12]}"

        logger.info(
            "discord.generate.start",
            request_id=request_id,
            user_id=user_id,
            prompt=prompt,
        )

        try:
            await redis_set_status(request_id, "pending")
            run_pipeline_background(request_id, user_id, prompt)

            await interaction.followup.send(
                f"Started generation! Request ID: `{request_id}`\nUse `/status {request_id}` to check progress.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("discord.generate.error", request_id=request_id, error=str(exc))
            await interaction.followup.send(
                f"Failed to start generation: {exc}",
                ephemeral=True,
            )

    @_bot.tree.command(
        name="status",
        description="Check the status of a generation request",
    )
    @app_commands.describe(request_id="The request ID to check")
    async def status_command(interaction: discord.Interaction, request_id: str) -> None:
        from storage.redis import get_status as redis_get_status

        await interaction.response.defer(ephemeral=True)

        current_status = await redis_get_status(request_id)
        if current_status is None:
            await interaction.followup.send(
                f"Status unknown for request `{request_id}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Request `{request_id}`: **{current_status}**",
            ephemeral=True,
        )

    @_bot.event
    async def on_ready() -> None:
        logger.info("discord.bot.ready", user=str(_bot.user), bot_id=_bot.user.id if _bot.user else None)

    logger.info("discord.bot.starting", app_id=config.discord_app_id or "global")
    await asyncio.wait_for(_bot.start(token), timeout=30)


def get_bot() -> commands.Bot | None:
    return _bot