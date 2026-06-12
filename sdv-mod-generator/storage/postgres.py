"""PostgreSQL storage — async SQLAlchemy."""
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger()

_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods")

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_init_lock = threading.Lock()


def get_engine() -> "AsyncEngine":
    """Return the singleton async SQLAlchemy engine, creating it if needed."""
    global _engine
    if _engine is None:
        with _init_lock:
            if _engine is None:
                _engine = create_async_engine(
                    _DATABASE_URL,
                    echo=False,
                    pool_size=10,
                    max_overflow=20,
                    pool_pre_ping=True,
                )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        with _init_lock:
            if _session_factory is None:
                _session_factory = async_sessionmaker(
                    bind=get_engine(),
                    class_=AsyncSession,
                    expire_on_commit=False,
                )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Get an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database schema if tables don't exist."""
    from pathlib import Path as PathLib

    logger.info("storage.postgres.init_db", database_url=_DATABASE_URL.split("@")[1] if "@" in _DATABASE_URL else "local")
    init_sql_path = PathLib(__file__).parent.parent / "db" / "init.sql"
    if not init_sql_path.exists():
        logger.error("storage.postgres.init_db.missing_sql", path=str(init_sql_path))
        raise FileNotFoundError(f"Database init SQL not found: {init_sql_path}")
    sql = init_sql_path.read_text()

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
