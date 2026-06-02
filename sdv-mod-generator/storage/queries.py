"""Database query helpers for mod_requests, mod_outputs, mod_history."""
from typing import Any

import structlog
from sqlalchemy import insert, select, text

from storage.postgres import get_session
from storage.models import ModRequest, ModOutput

logger = structlog.get_logger()


async def create_mod_request(
    request_id: str,
    user_id: str,
    prompt: str,
    phase: str,
    generators: list[str],
    hint: dict,
) -> None:
    async with get_session() as session:
        await session.execute(
            insert(ModRequest).values(
                request_id=request_id,
                user_id=user_id,
                prompt=prompt,
                phase=phase,
                generators=generators,
                hint=hint,
                status="pending",
            )
        )


async def update_mod_request_status(
    request_id: str,
    status: str,
) -> None:
    async with get_session() as session:
        await session.execute(
            text("""
                UPDATE mod_requests
                SET status = :status, updated_at = NOW()
                WHERE request_id = :request_id
            """),
            {"request_id": request_id, "status": status},
        )


async def save_mod_output(
    request_id: str,
    zip_key: str | None,
    zip_url: str | None,
    files_preview: list[str],
    t1_errors: list[str],
    t2_feedback: str | None,
    t2_score: int | None,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    async with get_session() as session:
        await session.execute(
            pg_insert(ModOutput).values(
                request_id=request_id,
                zip_key=zip_key,
                zip_url=zip_url,
                files_preview=files_preview,
                t1_errors=t1_errors,
                t2_feedback=t2_feedback,
                t2_score=t2_score,
            ).on_conflict_do_update(
                index_elements=["request_id"],
                set_={
                    "zip_key": zip_key,
                    "zip_url": zip_url,
                    "files_preview": files_preview,
                    "t1_errors": t1_errors,
                    "t2_feedback": t2_feedback,
                    "t2_score": t2_score,
                },
            )
        )


async def get_mod_output(request_id: str) -> dict[str, Any] | None:
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT mo.zip_key, mo.zip_url, mo.files_preview, mo.t1_errors,
                       mo.t2_feedback, mo.t2_score, mo.created_at,
                       mr.request_id, mr.prompt, mr.phase, mr.status, mr.user_id
                FROM mod_outputs mo
                JOIN mod_requests mr ON mo.request_id = mr.request_id
                WHERE mr.request_id = :request_id
            """),
            {"request_id": request_id},
        )
        row = result.fetchone()
        if not row:
            return None
        return {
            "zip_key": row.zip_key,
            "zip_url": row.zip_url,
            "files_preview": row.files_preview if isinstance(row.files_preview, list) else [],
            "t1_errors": row.t1_errors if isinstance(row.t1_errors, list) else [],
            "t2_feedback": row.t2_feedback,
            "t2_score": row.t2_score,
            "created_at": row.created_at,
            "request_id": row.request_id,
            "prompt": row.prompt,
            "phase": row.phase,
            "status": row.status,
            "user_id": row.user_id,
        }


async def get_user_history(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT mr.request_id, mr.prompt, mr.status, mr.created_at
                FROM mod_requests mr
                WHERE mr.user_id = :user_id
                ORDER BY mr.created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        )
        rows = result.fetchall()
        return [
            {
                "request_id": row.request_id,
                "prompt": row.prompt,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in rows
        ]
