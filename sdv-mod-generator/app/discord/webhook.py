"""Discord webhook endpoint for interaction callbacks."""
import hashlib
import hmac
import json
import os
from typing import Any

import structlog
from fastapi import Request, HTTPException, status

logger = structlog.get_logger()

_DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "")


def verify_signature(body: bytes, signature: str, timestamp: str) -> bool:
    if not _DISCORD_PUBLIC_KEY:
        logger.warning("discord.webhook.verify_signature.failed", reason="missing_public_key")
        return False
    if not signature or not timestamp:
        return False
    msg = f"{timestamp}{body.decode('utf-8')}".encode()
    expected = hmac.new(_DISCORD_PUBLIC_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


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