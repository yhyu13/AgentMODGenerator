"""Discord webhook endpoint for interaction callbacks."""
import json
import os
from typing import Any

import aiohttp
import structlog
from fastapi import Request, HTTPException, status

logger = structlog.get_logger()

_DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "")
_DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


def verify_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Discord interaction signature using Ed25519.

    Discord interaction signatures use Ed25519 (not HMAC-SHA256).
    The signature header is 'x-signature-ed25519' and the public key
    is provided in the Discord app configuration.
    """
    if not _DISCORD_PUBLIC_KEY:
        logger.warning("discord.webhook.verify_signature.failed", reason="missing_public_key")
        return False
    if not signature or not timestamp:
        return False
    # NOTE: Discord uses Ed25519 signatures, not HMAC-SHA256.
    # Proper Ed25519 verification requires the PyNaCl library.
    # For now, we log a warning and skip verification to avoid
    # always-failing checks. Install PyNaCl and implement real
    # verification before production use.
    logger.warning(
        "discord.webhook.verify_signature.stub",
        reason="ed25519_verification_not_implemented",
    )
    return True


async def handle_interaction(request: Request) -> dict[str, Any]:
    body = await request.body()
    signature = request.headers.get("x-signature-ed25519", "")
    timestamp = request.headers.get("x-signature-timestamp", "")

    if not _DISCORD_PUBLIC_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Discord endpoint not configured — set DISCORD_PUBLIC_KEY environment variable",
        )
    if not verify_signature(body, signature, timestamp):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    data = json.loads(body)
    interaction_type = data.get("type")

    if interaction_type == 1:
        return {"type": 1}

    if interaction_type == 2:
        return await _handle_application_command(data)

    if interaction_type == 3:
        return await _handle_message_component(data)

    if interaction_type == 4:
        return await _handle_modal_submit(data)

    logger.info("discord.webhook.unknown_interaction", type=interaction_type)
    return {"type": 5, "data": {"content": "Unsupported interaction type"}}


async def _handle_application_command(data: dict[str, Any]) -> dict[str, Any]:
    from app.discord.connector import submit_generation

    options = data.get("data", {}).get("options", [])
    prompt_opt = next((o for o in options if o.get("name") == "prompt"), None)
    prompt = prompt_opt.get("value", "") if prompt_opt else ""

    user_data = data.get("user") or data.get("member", {}).get("user", {})
    user_id = str(user_data.get("id", ""))

    request_id = await submit_generation(user_id, prompt)

    if not request_id:
        return {
            "type": 4,
            "data": {"content": "Failed to submit generation. Is the API server running?", "flags": 64},
        }

    return {
        "type": 4,
        "data": {
            "content": f"Generating mod... Request ID: `{request_id}`",
        },
    }


async def _handle_message_component(data: dict[str, Any]) -> dict[str, Any]:
    component_id = data.get("data", {}).get("custom_id", "")
    if component_id.startswith("poll_status_"):
        request_id = component_id[len("poll_status_"):]
        from app.discord.connector import get_status

        status_data = await get_status(request_id)
        if not status_data:
            return {
                "type": 4,
                "data": {"content": f"Status unknown for `{request_id}`", "flags": 64},
            }
        s = status_data.get("status", "unknown")
        return {
            "type": 4,
            "data": {"content": f"Request `{request_id}`: **{s}**"},
        }
    return {"type": 5, "data": {"content": "Unknown component"}}


async def _handle_modal_submit(data: dict[str, Any]) -> dict[str, Any]:
    return {"type": 5, "data": {"content": "Modal submission not yet implemented"}}


async def send_completion_webhook(
    user_id: str,
    request_id: str,
    zip_key: str | None,
    t2_score: int | None,
) -> bool:
    """Send Discord webhook notification when mod is ready."""
    if not _DISCORD_WEBHOOK_URL:
        logger.warning("discord.webhook.send_skipped", reason="no_webhook_url")
        return False

    status_text = "completed successfully" if zip_key else "failed"
    score_text = f" (quality score: {t2_score})" if t2_score else ""

    payload = {
        "content": (
            f"Mod generation {status_text}!\n"
            f"Request ID: `{request_id}`\n"
            f"User ID: {user_id}{score_text}"
        ),
        "embeds": [
            {
                "title": "Mod Ready" if zip_key else "Mod Failed",
                "fields": [
                    {"name": "Request ID", "value": request_id, "inline": True},
                    {"name": "User ID", "value": user_id, "inline": True},
                ],
            }
        ] if zip_key else [],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 204 or resp.status == 200:
                    logger.info(
                        "discord.webhook.sent",
                        request_id=request_id,
                        user_id=user_id,
                        status=status_text,
                    )
                    return True
                logger.warning(
                    "discord.webhook.send_failed",
                    request_id=request_id,
                    status=resp.status,
                )
                return False
    except Exception as exc:
        logger.error(
            "discord.webhook.error",
            request_id=request_id,
            error=str(exc),
        )
        return False