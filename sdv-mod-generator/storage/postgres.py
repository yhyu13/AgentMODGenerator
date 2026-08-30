"""PostgreSQL storage layer — async SQLAlchemy engine and session helpers.

This module owns the singleton :class:`AsyncEngine` and the
:class:`async_sessionmaker` used by every other storage submodule
(``storage.queries``, the orchestrator's DB writes, the API route
handlers, etc.). The design is intentionally tiny: a lazily-created
engine plus a session context-manager that handles commit / rollback /
close in a single ``async with`` block. Callers should never construct
their own engine — they should call :func:`get_engine` or, more
commonly, :func:`get_session`.

Initialization reads the ``DATABASE_URL`` environment variable at
call time (via :func:`_database_url`) and falls back to a local
docker-compose default. The connection string is treated as a
secret: the ``engine_created`` / ``init_db`` log lines only emit the
host portion (after ``@``) so credentials never reach the structured
logs. v74 Blue moved the read off module-top into a per-call helper
so monkeypatch-set values are observed without reloading the module.
"""
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger(__name__)


def _database_url() -> str:
    """Return the current ``DATABASE_URL`` env var, falling back to the local default.

    Read at call time (not at module import time) so tests that
    ``monkeypatch.setenv("DATABASE_URL", ...)`` after import see the
    patched value on the very next ``get_engine()`` call. Mirrors
    the per-call read pattern used by the v73 ``_local_output_dir()``
    helper in ``generators/packager.py`` and the v72 discord helpers.

    Returns:
        str: The current ``DATABASE_URL`` value. Returns the docker-compose
        dev default (``postgresql+asyncpg://postgres:postgres@localhost:
        5432/sdv_mods``) when the env is unset. Returns ``""`` when the
        env is set but explicitly empty (matches permissive
        ``os.getenv`` semantics so callers can opt out cleanly).
    """
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods",
    )


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
# RLock (not Lock): get_session_factory() holds the lock while calling
# get_engine(), which acquires it again for the engine's own lazy
# creation. A plain threading.Lock deadlocks on the first DB touch when
# the engine hasn't been created yet (the classic nested-lock hang —
# observed in the MVP audit on a host with no Postgres running: the
# first get_session() call blocked forever).
_init_lock = threading.RLock()


def _reset_engine_for_tests() -> None:
    """Reset the cached engine and session factory. Tests-only.

    Called from the test suite (via conftest fixtures) after
    monkeypatching ``DATABASE_URL`` so the next ``get_engine()``
    rebuilds the engine with the new URL. Does nothing to a live
    engine that is currently in use — production code never calls
    this.
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


def get_engine() -> "AsyncEngine":
    """Return the singleton async SQLAlchemy engine, creating it if needed.

    Pool configuration:

    * ``pool_size=10`` — base number of persistent connections.
    * ``max_overflow=20`` — burst capacity for short-lived spikes.
    * ``pool_pre_ping=True`` — silently recycle stale connections before
      handing them out, which avoids the
      ``InvalidRequestError: This Connection is closed`` class of
      failures when the Postgres side restarts underneath us.

    Emits ``storage.postgres.engine_created`` at INFO level the first
    time the engine is constructed so operators can confirm the bound
    ``DATABASE_URL`` (host portion only, never credentials) without
    re-reading the source. The log line is not emitted on subsequent
    cached returns.
    """
    global _engine
    if _engine is None:
        url = _database_url()
        with _init_lock:
            if _engine is None:
                _engine = create_async_engine(
                    url,
                    echo=False,
                    pool_size=10,
                    max_overflow=20,
                    pool_pre_ping=True,
                    # Bound the asyncpg connection attempt. asyncpg's
                    # default connect timeout is 60s; on some platforms
                    # (notably Windows) a refused connection can hang
                    # well past that, blocking the request that first
                    # touches the pool. 5s bounds the failure fast and
                    # surfaces "Postgres is down" instead of a stalled
                    # request.
                    connect_args={"timeout": 5},
                )
                # host-only disclosure so credentials never reach the
                # structured logs (matches the v19 Blue pattern in
                # get_session()'s session_rollback).
                logger.info(
                    "storage.postgres.engine_created",
                    database_url=(url.split("@")[1] if "@" in url else "local"),
                )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton async session factory, creating it on first call.

    Bound to the engine produced by :func:`get_engine` and configured
    with ``expire_on_commit=False`` so that detached ORM objects remain
    usable after the ``async with`` block exits (the API route handlers
    depend on this when serializing rows into Pydantic response models).

    Emits ``storage.postgres.session_factory_created`` at INFO level on
    first construction so the factory lifecycle is visible in the
    structured log stream.
    """
    global _session_factory
    if _session_factory is None:
        with _init_lock:
            if _session_factory is None:
                _session_factory = async_sessionmaker(
                    bind=get_engine(),
                    class_=AsyncSession,
                    expire_on_commit=False,
                )
                logger.info("storage.postgres.session_factory_created")
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Get an async DB session.

    Manages ``commit`` / ``rollback`` / ``close`` in a single
    ``async with`` block:

    * Commits on a clean exit of the body.
    * Rolls back on any exception, emitting a
      ``storage.postgres.session_rollback`` structured log line
      with ``error`` (string form) and ``error_type`` (exception
      class name) so operators can group rollback failures by
      exception class via the v19 Blue meta-lint pattern.
    * Closes in the ``finally`` branch regardless of whether the
      body succeeded, raised, or was cancelled.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            # v19 Blue: meta-lint compliance — surface error_type for log
            # aggregation (group rollback failures by exception class).
            # Behavior unchanged: we still rollback + re-raise.
            logger.warning(
                "storage.postgres.session_rollback",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database schema if tables don't exist."""
    from pathlib import Path as PathLib

    db_url = _database_url()
    logger.info("storage.postgres.init_db", database_url=(db_url.split("@")[1] if "@" in db_url else "local"))
    init_sql_path = PathLib(__file__).parent.parent / "db" / "init.sql"
    if not init_sql_path.exists():
        logger.error("storage.postgres.init_db.missing_sql", path=str(init_sql_path))
        raise FileNotFoundError(f"Database init SQL not found: {init_sql_path}")
    sql = init_sql_path.read_text(encoding="utf-8")

    engine = get_engine()
    async with engine.begin() as conn:
        # Split by semicolon but preserve statements that contain semicolons
        # inside string literals. A simple split is sufficient for our
        # init.sql which only uses semicolons as statement terminators.
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            from sqlalchemy import text
            await conn.execute(text(stmt))

    logger.info("storage.postgres.init_db.done")


async def close_pool() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("storage.postgres.closed")
