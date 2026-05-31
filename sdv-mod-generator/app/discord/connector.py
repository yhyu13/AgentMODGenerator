"""Discord connector — bridges Discord interactions to the generation pipeline."""
import asyncio
import os
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger()

_API_BASE = os.getenv("DISCORD_API_BASE", "http://localhost:8000")


async def submit_generation(user_id: str, prompt: str, phase: str | None = None) -> str | None:
    payload: dict[str, Any] = {"user_id": user_id, "prompt": prompt}
    if phase:
        payload["phase"] = phase
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_API_BASE}/v1/mods/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning("connector.submit.failed", status=resp.status, user_id=user_id)
                    return None
                data = await resp.json()
                return data.get("request_id")
    except Exception as exc:
        logger.error("connector.submit.error", user_id=user_id, error=str(exc))
        return None


async def get_status(request_id: str, session: aiohttp.ClientSession | None = None) -> dict[str, Any] | None:
    own_session = session is None
    try:
        if own_session:
            session = aiohttp.ClientSession()
        async with session.get(
            f"{_API_BASE}/v1/mods/{request_id}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as exc:
        logger.warning("connector.status.error", request_id=request_id, error=str(exc))
        return None
    finally:
        if own_session:
            await session.close()


async def poll_until_done(request_id: str, interval: float = 2.0, max_polls: int = 120) -> tuple[str, str | None]:
    async with aiohttp.ClientSession() as session:
        for _ in range(max_polls):
            status_data = await get_status(request_id, session)
            if status_data:
                status = status_data.get("status", "pending")
                if status in ("done", "failed"):
                    zip_key = status_data.get("zip_url", "") or ""
                    if zip_key.startswith("file://"):
                        import posixpath
                        zip_key = posixpath.basename(zip_key[7:])
                    return status, zip_key
            await asyncio.sleep(interval)
    return "failed", None


def local_zip_path(zip_key: str) -> str:
    local_dir = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")
    return os.path.join(local_dir, zip_key)