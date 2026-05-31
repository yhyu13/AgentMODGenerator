"""PostgreSQL storage — async SQLAlchemy."""
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger()

_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods"

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(_DATABASE_URL, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
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
    sql = init_sql_path.read_text()

    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                from sqlalchemy import text
                await conn.execute(text(stmt))

    logger.info("storage.postgres.init_db.done")


async def close_pool() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("storage.postgres.closed")
