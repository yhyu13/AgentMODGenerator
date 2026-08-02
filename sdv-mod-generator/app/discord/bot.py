"""Discord bot client."""

import asyncio
import uuid
import structlog
import discord
from discord import app_commands
from discord.ext import commands

from app.config import get_config
from app.discord.notifier import CompletionNotifier
from orchestrator.pipeline import run_pipeline_background
from storage.redis import set_notification_target, set_status as redis_set_status

logger = structlog.get_logger()

_intents = discord.Intents.default()
_intents.messages = True
_intents.message_content = True

_bot: commands.Bot | None = None
_bot_ready: asyncio.Event = asyncio.Event()
_notifier: CompletionNotifier | None = None


def get_notifier() -> CompletionNotifier | None:
    return _notifier


def _extract_prompt_from_message(content: str) -> str | None:
    """Decide whether a chat message is a free-form mod request.

    Returns the prompt to pipeline (the trimmed message) or ``None`` when
    the message should not trigger generation: empty, a command (``!`` /
    ``/`` prefixes — slash commands are handled by the tree), a greeting,
    or too short to be a mod description (20-char heuristic avoids firing
    on casual chat).

    Extracted from the ``on_message`` handler so the intake rules are
    unit-testable without a live Discord gateway connection.
    """
    content = content.strip()
    if not content or content.startswith(("!", "/")):
        return None
    if content.lower() in ("hi", "hello", "hey", "你好", "嗨"):
        return None
    if len(content) < 20:
        return None
    return content


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
        prompt = _extract_prompt_from_message(message.content)
        if prompt is None:
            if message.content.strip().lower() in ("hi", "hello", "hey", "你好", "嗨"):
                await message.channel.send(
                    "Hello! I'm Agent Mod 0x01. Use `/generate <prompt>` or just describe your mod "
                    "in chat to create a Stardew Valley mod."
                )
            return
        # Free-form intake: any non-trivial message is treated as a mod
        # request. The request reuses the same background-pipeline +
        # DM-notifier path as /generate.
        user_id = str(message.author.id)
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        logger.info(
            "discord.message.pipeline_triggered",
            request_id=request_id,
            user_id=user_id,
            prompt=prompt,
        )
        try:
            await redis_set_status(request_id, "pending")
            await set_notification_target(
                request_id,
                user_id=user_id,
                channel_id=message.channel.id,
            )
            run_pipeline_background(request_id, user_id, prompt)
            await message.channel.send(
                f"Started generating your mod! Request ID: `{request_id}`\n"
                "I'll DM you a link when it's ready. Use `/status {request_id}` to check progress."
            )
        except Exception as exc:
            logger.error(
                "discord.message.pipeline_error",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await message.channel.send(f"Failed to start generation: {exc}")

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
            await set_notification_target(
                request_id,
                user_id=user_id,
                channel_id=interaction.channel_id or 0,
            )
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
        from storage.redis import get_status as redis_get_status, get_pipeline_state

        await interaction.response.defer(ephemeral=True)

        current_status = await redis_get_status(request_id)
        if current_status is None:
            await interaction.followup.send(
                f"Status unknown for request `{request_id}`",
                ephemeral=True,
            )
            return

        # Try to get richer status with progress
        progress_text = ""
        try:
            state = await get_pipeline_state(request_id)
            if state:
                from app.api.routes import _compute_progress
                progress = _compute_progress(state)
                progress_text = f" ({progress['percent']}% - {progress['stage']})"
        except Exception:
            pass

        await interaction.followup.send(
            f"Request `{request_id}`: **{current_status}**{progress_text}",
            ephemeral=True,
        )

    @_bot.tree.command(
        name="cancel",
        description="Cancel a running generation request",
    )
    @app_commands.describe(request_id="The request ID to cancel")
    async def cancel_command(interaction: discord.Interaction, request_id: str) -> None:
        from storage.redis import get_pipeline_state

        await interaction.response.defer(ephemeral=True)

        state = await get_pipeline_state(request_id)
        if not state:
            await interaction.followup.send(
                f"Request `{request_id}` not found.",
                ephemeral=True,
            )
            return

        current_status = state.get("status", "unknown")
        if current_status in ("done", "failed"):
            await interaction.followup.send(
                f"Cannot cancel request `{request_id}` — it is already **{current_status}**.",
                ephemeral=True,
            )
            return

        await redis_set_status(request_id, "cancelled")
        from orchestrator.pipeline import cancel_pipeline_task

        if not cancel_pipeline_task(request_id):
            logger.info(
                "discord.cancel.no_task",
                request_id=request_id,
                previous_status=current_status,
            )
        logger.info(
            "discord.cancel.done",
            request_id=request_id,
            user_id=str(interaction.user.id),
            previous_status=current_status,
        )
        await interaction.followup.send(
            f"Cancelled request `{request_id}` (was **{current_status}**).",
            ephemeral=True,
        )

    @_bot.tree.command(
        name="history",
        description="Show your recent mod generation history",
    )
    async def history_command(interaction: discord.Interaction) -> None:
        """Show user's recent generation history with rich embeds."""
        from storage.queries import get_user_history

        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        try:
            entries = await get_user_history(user_id)
        except Exception as exc:
            logger.error("discord.history.error", user_id=user_id, error=str(exc))
            await interaction.followup.send(
                "Failed to load history. Please try again later.",
                ephemeral=True,
            )
            return

        if not entries:
            await interaction.followup.send(
                "You haven't generated any mods yet. Use `/generate` to create one!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Mod Generation History",
            description=f"Recent requests for {interaction.user.display_name}",
            color=discord.Color.blue(),
        )
        for entry in entries[:10]:
            status_emoji = {
                "done": "✅",
                "failed": "❌",
                "running": "⏳",
                "pending": "⏳",
                "cancelled": "🚫",
            }.get(entry.get("status", "unknown"), "❓")
            prompt_short = entry.get("prompt", "")[:60]
            if len(entry.get("prompt", "")) > 60:
                prompt_short += "..."
            embed.add_field(
                name=f"{status_emoji} `{entry['request_id']}`",
                value=f"{prompt_short}\nStatus: **{entry.get('status', 'unknown')}**",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @_bot.event
    async def on_ready() -> None:
        global _notifier
        logger.info("discord.bot.ready", user=str(_bot.user), bot_id=_bot.user.id if _bot.user else None)
        _bot_ready.set()
        _notifier = CompletionNotifier(_bot)
        _notifier.start()

    logger.info("discord.bot.starting", app_id=config.discord_app_id or "global")
    # No timeout: bot.start() is the long-lived connection. A 30s wait_for
    # previously killed the bot before the SOCKS5 proxy + WebSocket
    # handshake completed (verified in P4.6 task 3 — on_ready fires
    # after ~30-40s through the proxy). If the token is bad,
    # discord.errors.LoginFailure is raised on the first call instead.
    try:
        await _bot.start(token)
    except discord.errors.LoginFailure as exc:
        logger.error("discord.bot.login_failed", error=str(exc))
        raise
    except Exception as exc:
        logger.error("discord.bot.start.failed", error=str(exc), error_type=type(exc).__name__)
        raise


def get_bot() -> commands.Bot | None:
    return _bot


def is_bot_ready() -> bool:
    """Whether the Discord bot has reached on_ready.

    Used by /health to surface whether the bot is actually connected,
    not just whether the lifespan task is alive (P4.6 task 3).
    """
    return _bot_ready.is_set()