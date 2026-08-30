"""Database query helpers for mod_requests, mod_outputs, mod_history."""
from typing import Any

import structlog
from sqlalchemy import text

from storage.postgres import get_session
from storage.models import ModRequest, ModOutput
from storage.status_validation import VALID_MOD_STATUSES

logger = structlog.get_logger()


async def create_mod_request(
    request_id: str,
    user_id: str,
    prompt: str,
    phase: str,
    generators: list[str],
    # v42 Blue: tightened from bare ``dict`` to match ``Mapped[dict[str, Any]]``
    # declared on ``ModRequest.hint`` (storage/models/models.py L50). The bare
    # annotation accepted any object at the type level, masking contract drift
    # if a caller passed ``list[dict]`` or a plain ``str``. The ``get_session``
    # ORM insert path serialises the value as JSON, so the narrower shape is
    # honored both at type-check time and at runtime.
    hint: dict[str, Any],
) -> None:
    # Idempotent: the API route and the Discord-bot path both call this
    # (the bot via ``run_pipeline_background`` after routing, the API
    # before launching the pipeline). A second insert with the same
    # request_id must be a no-op, otherwise the bot path's post-routing
    # ensure-row would collide with the API's pre-launch row. See
    # ``_run_pipeline_and_update_status`` in orchestrator/pipeline.py.
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with get_session() as session:
        await session.execute(
            pg_insert(ModRequest).values(
                request_id=request_id,
                user_id=user_id,
                prompt=prompt,
                phase=phase,
                generators=generators,
                hint=hint,
                status="pending",
            ).on_conflict_do_nothing(index_elements=["request_id"])
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


# Canonical sort orders exposed by ``list_mod_requests``. Keys are the
# public ``sort`` query param values (snake_case per AGENTS.md);
# values are the corresponding ``ORDER BY`` SQL fragments. Adding a
# new sort mode here is the single source of truth — the route layer
# validates against this dict so a typo in the route can't drift
# from the SQL we actually execute.
_LIST_SORT_ORDERS: dict[str, str] = {
    "created_at_desc": "mr.created_at DESC",
    "created_at_asc": "mr.created_at ASC",
    "updated_at_desc": "mr.updated_at DESC NULLS LAST",
}


async def list_mod_requests(
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "created_at_desc",
) -> list[dict[str, Any]]:
    """List mod_requests rows with optional filters.

    A pure read-only helper used by the ``GET /v1/mods`` listing
    endpoint. The query is bounded by ``limit`` (the route layer caps
    that at 100), starts at ``offset``, and is ordered per ``sort``
    (default: newest first by ``created_at``).

    Args:
        user_id: If provided, restrict to requests created by this user.
            ``None`` (the default) means "all users" — the route layer
            should require an explicit ``user_id`` filter when the
            endpoint is exposed in production (currently it does not).
        status: If provided, restrict to requests in this status. Must
            be one of :data:`storage.status_validation.VALID_MOD_STATUSES`
            if supplied; anything else would match nothing in practice
            but we still validate at the route layer (Pydantic Literal)
            so a bad value never reaches this function.
        limit: Maximum rows to return. The caller is responsible for
            clamping this to a sane upper bound; we trust the route
            layer to do that.
        offset: Number of rows to skip before returning results. The
            caller (route layer) is responsible for clamping this to
            a sane value (``>= 0``). Defaults to 0.
        sort: Sort order key. Must be one of :data:`_LIST_SORT_ORDERS`.
            Defaults to ``"created_at_desc"`` (newest first).

    Returns:
        A list of plain ``dict`` rows, each with the keys
        ``request_id``, ``user_id``, ``status``, ``phase``,
        ``created_at``, ``updated_at``, ``prompt``, and ``zip_key``.
        ``created_at`` and ``updated_at`` are always :class:`datetime`
        (never a string) because we read them through SQLAlchemy's
        typed ``DateTime(timezone=True)`` columns; ``zip_key`` is
        ``None`` for requests that have no ``mod_outputs`` row yet
        (the LEFT JOIN keeps those rows in the listing so in-flight
        requests still appear).

    Raises:
        ValueError: if ``status`` is provided but not in
            :data:`storage.status_validation.VALID_MOD_STATUSES`, or
            if ``sort`` is not a key in :data:`_LIST_SORT_ORDERS`.
            We raise here (rather than silently returning nothing) so
            a programming bug that bypasses the route-layer validation
            still surfaces.
    """
    if status is not None and status not in VALID_MOD_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}; must be one of "
            f"{sorted(VALID_MOD_STATUSES)}"
        )
    if sort not in _LIST_SORT_ORDERS:
        raise ValueError(
            f"Invalid sort {sort!r}; must be one of "
            f"{sorted(_LIST_SORT_ORDERS)}"
        )
    order_sql = _LIST_SORT_ORDERS[sort]

    # Build the WHERE clause incrementally so we never inject
    # user-controlled strings into the SQL — both filters are
    # parameterized.
    where_clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if user_id is not None:
        where_clauses.append("mr.user_id = :user_id")
        params["user_id"] = user_id
    if status is not None:
        where_clauses.append("mr.status = :status")
        params["status"] = status
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # LEFT JOIN keeps requests with no mod_outputs row yet (e.g. still
    # in 'running' state) so the listing can show in-flight requests
    # with zip_key=None.
    sql = text(f"""
        SELECT mr.request_id, mr.user_id, mr.status, mr.phase,
               mr.created_at, mr.updated_at, mr.prompt,
               mo.zip_key
        FROM mod_requests mr
        LEFT JOIN mod_outputs mo ON mo.request_id = mr.request_id
        {where_sql}
        ORDER BY {order_sql}
        LIMIT :limit OFFSET :offset
    """)

    async with get_session() as session:
        result = await session.execute(sql, params)
        rows = result.fetchall()

    return [
        {
            "request_id": row.request_id,
            "user_id": row.user_id,
            "status": row.status,
            "phase": row.phase,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "prompt": row.prompt,
            "zip_key": row.zip_key,
        }
        for row in rows
    ]


async def count_mod_requests(
    user_id: str | None = None,
    status: str | None = None,
) -> int:
    """Return the total number of ``mod_requests`` rows matching the filters.

    Companion helper to :func:`list_mod_requests` used by the
    ``GET /v1/mods`` route to compute ``total`` and ``has_more`` for
    the response envelope. Uses the same WHERE-clause builder as
    :func:`list_mod_requests` (minus LIMIT/OFFSET/sort) so the count
    and the page can never drift apart on filter semantics.

    Args:
        user_id: If provided, restrict to requests created by this user.
        status: If provided, restrict to requests in this status. Must
            be one of :data:`storage.status_validation.VALID_MOD_STATUSES`
            if supplied; the route layer's Pydantic Literal is the
            primary validation gate, but we re-check here so a
            programming bug that bypasses the route still surfaces.

    Returns:
        Non-negative integer count of matching rows. Returns 0 if the
        underlying query yields no row (defensive — in practice the
        ``COUNT(*)`` aggregate always yields exactly one row, but we
        don't want to crash the route on a hypothetical empty result).

    Raises:
        ValueError: if ``status`` is provided but not in
            :data:`storage.status_validation.VALID_MOD_STATUSES`. Same
            contract as :func:`list_mod_requests` for symmetry.
    """
    if status is not None and status not in VALID_MOD_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}; must be one of "
            f"{sorted(VALID_MOD_STATUSES)}"
        )

    # Build the WHERE clause incrementally so we never inject
    # user-controlled strings into the SQL — both filters are
    # parameterized. Mirrors list_mod_requests's WHERE builder.
    where_clauses: list[str] = []
    params: dict[str, Any] = {}
    if user_id is not None:
        where_clauses.append("mr.user_id = :user_id")
        params["user_id"] = user_id
    if status is not None:
        where_clauses.append("mr.status = :status")
        params["status"] = status
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM mod_requests mr
        {where_sql}
    """)

    async with get_session() as session:
        result = await session.execute(sql, params)
        row = result.fetchone()
    return int(row.cnt) if row is not None else 0


# Synthetic key used by ``get_mod_request_stats`` to surface requests
# with ``phase IS NULL`` in the ``by_phase`` breakdown. Using a fixed
# string (rather than skipping them or surfacing ``None`` as JSON
# ``null``) keeps the response shape JSON-friendly and the test suite
# easy to write.
_STATS_NULL_PHASE_KEY: str = "__none__"


async def get_mod_request_stats() -> dict[str, Any]:
    """Return aggregate mod-request stats for the ``GET /v1/mods/stats`` endpoint.

    A pure read-only helper that returns three numbers in one DB
    session's worth of queries (count + status GROUP BY + phase
    GROUP BY) so the route can build a :class:`StatsResponse` cheaply.

    Returns:
        A dict with three keys:

        - ``total``: total mod_requests row count (int).
        - ``by_status``: list of ``{"status": str, "count": int}``
          dicts, sorted by count descending (then status ascending
          for determinism). Statuses with zero rows are omitted.
        - ``by_phase``: list of ``{"phase": str, "count": int}``
          dicts, sorted by count descending (then phase ascending
          for determinism). Rows with ``phase IS NULL`` are
          surfaced under :data:`_STATS_NULL_PHASE_KEY` (``"__none__"``)
          rather than dropped.

    The shape mirrors :class:`StatsResponse` exactly, but the route
    still does the dict→Pydantic mapping so the response model is
    the single source of truth for the public contract.

    Notes:
        - No filters, no pagination, no auth — this is a global
          operator view. If you need per-user or per-tenant stats,
          add a parameterized variant; do not overload this one.
        - The two ``GROUP BY`` queries are independent: if the
          status GROUP BY succeeds but the phase one fails (e.g.
          transient DB error), the exception propagates and the
          caller sees a 500. That's the right behavior — a partial
          breakdown would be misleading.
    """
    async with get_session() as session:
        # 1) Total count — a single COUNT(*) is cheap with the
        # primary-key scan and is what dashboards actually plot.
        total_result = await session.execute(
            text("SELECT COUNT(*) FROM mod_requests"),
        )
        total_row = total_result.fetchone()
        total_value: int = int(total_row[0]) if total_row is not None else 0

        # 2) Status breakdown — single GROUP BY, status with zero
        # rows simply doesn't appear in the result set.
        status_result = await session.execute(
            text("""
                SELECT status, COUNT(*) AS cnt
                FROM mod_requests
                GROUP BY status
                ORDER BY cnt DESC, status ASC
            """),
        )
        status_rows = status_result.fetchall()
        by_status: list[dict[str, Any]] = [
            {"status": row.status, "count": int(row.cnt)}
            for row in status_rows
        ]

        # 3) Phase breakdown — COALESCE NULLs to the synthetic key so
        # the response shape is uniform. Sorted by count desc, then
        # phase asc for stable output.
        phase_result = await session.execute(
            text("""
                SELECT COALESCE(phase, :null_phase_key) AS phase_key,
                       COUNT(*) AS cnt
                FROM mod_requests
                GROUP BY phase_key
                ORDER BY cnt DESC, phase_key ASC
            """),
            {"null_phase_key": _STATS_NULL_PHASE_KEY},
        )
        phase_rows = phase_result.fetchall()
        by_phase: list[dict[str, Any]] = [
            {"phase": row.phase_key, "count": int(row.cnt)}
            for row in phase_rows
        ]

    return {
        "total": total_value,
        "by_status": by_status,
        "by_phase": by_phase,
    }


async def delete_old_mod_requests(days: int) -> list[str]:
    """Delete ``mod_requests`` rows older than ``days`` and return their ids.

    v105 Blue (Feature 4 — purge_old_mods admin command; companion to
    the v104 ``PurgeRequest`` + ``PurgeResponse`` Pydantic models). Thin
    wrapper around a single ``DELETE ... RETURNING`` statement so the
    HTTP ``POST /v1/mods/purge`` endpoint and a future Discord
    ``/purge`` command share one implementation. The matching
    ``mod_outputs`` rows (if any) are removed in the same transaction
    by relying on the existing ``ON DELETE CASCADE`` foreign key
    declared in :mod:`storage.models` — this helper does not need to
    issue a second DELETE against ``mod_outputs``.

    The function does NOT touch Redis state. The route and Discord
    caller are responsible for calling
    :func:`storage.redis.delete_pipeline_state` (and friends) for
    each id this helper returns, so a single purge operation cleans
    up both the SQL row and its Redis keys. The two-step design is
    intentional: the DB and Redis calls can fail independently, and
    the route layer can decide whether to surface a partial-cleanup
    error or absorb it (it absorbs it, mirroring the v45
    cancel-reason graceful-degrade pattern).

    Args:
        days: Minimum age in days. Rows with ``created_at`` older
            than ``NOW() - INTERVAL 'days days'`` are deleted. Must
            be ``>= 1``; the route layer enforces
            ``1 <= days <= 365`` via Pydantic, so this helper does
            not re-validate but will return an empty list for
            ``days <= 0`` to keep internal callers (tests) safe.

    Returns:
        list[str]: The ``request_id`` of every row that was
        deleted. Empty list when no rows match (the common case on a
        healthy system — the operator can tell at a glance whether
        the purge actually removed anything without having to query
        the table afterwards).

    Emits:
        storage.queries.delete_old_mod_requests (INFO) with
        ``days`` and ``deleted_count`` so operators can audit purge
        operations from the log stream.

    Note:
        This is a destructive, irreversible operation. The
        :mod:`app.api.routes` ``POST /v1/mods/purge`` endpoint gates
        this helper behind the ``ADMIN_PURGE_ENABLED`` flag (default
        ``False``) plus :func:`app.api.routes.verify_api_key`, so it
        cannot be triggered accidentally from a public surface.
    """
    if days < 1:
        return []
    async with get_session() as session:
        result = await session.execute(
            text(
                """
                DELETE FROM mod_requests
                WHERE created_at < NOW() - (:days || ' days')::interval
                RETURNING request_id
                """
            ),
            {"days": int(days)},
        )
        rows = result.fetchall()
    deleted_ids: list[str] = [row.request_id for row in rows]
    logger.info(
        "storage.queries.delete_old_mod_requests",
        days=days,
        deleted_count=len(deleted_ids),
    )
    return deleted_ids
