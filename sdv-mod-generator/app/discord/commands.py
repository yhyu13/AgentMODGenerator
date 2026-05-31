"""Discord slash commands."""
import asyncio
import os
from pathlib import Path

import aiohttp
import discord
import structlog
from discord import app_commands
from discord import Interaction
from discord.ext import commands

logger = structlog.get_logger()

_API_BASE = os.getenv("DISCORD_API_BASE", "http://localhost:8000")
_POLL_INTERVAL = 2.0
_MAX_POLLS = 60


def setup_commands(bot: commands.Bot) -> None:
    bot.tree.add_command(generate_command)


@app_commands.command(
    name="generate",
    description="Generate a Stardew Valley mod from a text prompt",
)
@app_commands.describe(prompt="Describe the mod you want to create")
async def generate_command(
    interaction: Interaction,
    prompt: str,
) -> None:
    await interaction.response.defer(ephemeral=False)

    request_id = await _submit_generation(interaction.user.id, prompt)
    if not request_id:
        await interaction.followup.send(
            "Failed to submit generation request. Is the API server running?",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"Generating mod... Request ID: `{request_id}`\nThis may take a few minutes.",
        ephemeral=False,
    )

    status, zip_key = await _poll_until_done(request_id)

    if status == "failed":
        await interaction.followup.send(
            f"Generation failed for request `{request_id}`.",
            ephemeral=False,
        )
        return

    if status != "done" or not zip_key:
        await interaction.followup.send(
            f"Unexpected status: {status} for request `{request_id}`.",
            ephemeral=False,
        )
        return

    zip_path = _zip_local_path(zip_key)
    if not zip_path.exists():
        await interaction.followup.send(
            f"Mod generated but zip file not found: `{zip_key}`.",
            ephemeral=False,
        )
        return

    try:
        await interaction.followup.send(
            f"Mod ready! `{request_id}`",
            file=discord.File(zip_path, filename=f"{zip_key}"),
            ephemeral=False,
        )
    except Exception as exc:
        logger.error("discord.send_file.error", request_id=request_id, error=str(exc))
        await interaction.followup.send(
            f"Mod generated but failed to send zip: {exc}",
            ephemeral=False,
        )


async def _submit_generation(user_id: int, prompt: str) -> str | None:
    payload = {
        "user_id": str(user_id),
        "prompt": prompt,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_API_BASE}/v1/mods/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "discord.submit.failed",
                        status=resp.status,
                        user_id=user_id,
                    )
                    return None
                data = await resp.json()
                return data.get("request_id")
    except Exception as exc:
        logger.error("discord.submit.error", user_id=user_id, error=str(exc))
        return None


async def _poll_until_done(request_id: str) -> tuple[str, str | None]:
    headers = {"Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        for _ in range(_MAX_POLLS):
            try:
                async with session.get(
                    f"{_API_BASE}/v1/mods/{request_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 404:
                        await asyncio.sleep(_POLL_INTERVAL)
                        continue
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    status = data.get("status", "pending")
                    if status in ("done", "failed"):
                        zip_key = data.get("zip_url", "")
                        if zip_key and zip_key.startswith("file://"):
                            file_path = zip_key[7:]
                            if ".." in file_path:
                                raise ValueError(f"Path traversal in zip_url: {zip_key}")
                            zip_key = Path(file_path).name
                        return status, zip_key
            except Exception as exc:
                logger.warning("discord.poll.error", request_id=request_id, error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)
    return "failed", None


def _zip_local_path(zip_key: str) -> Path:
    local_dir = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")
    return Path(local_dir) / zip_key