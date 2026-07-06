"""Tests for the FastAPI ``app.main`` startup/shutdown lifespan.

The lifespan (app/main.py:23-184) is the entrypoint for every backend
component: prod-secrets validation, config validation, ``init_db``, the
v110/v111/v112/v113 prod-misconfig WARNING soft-warns for the four
bool-wrapped string secrets, conditional Discord bot task launch (with
a done-callback that logs crashes), then shutdown: cancel bot task,
close Redis client + Postgres pool. Pins every documented branch —
happy path, validate_config failure, init_db failure, bot lifecycle
with notifier, and graceful-degradation when close_pool or
close_client raises during shutdown.

Test strategy:
- Drive :func:`app.main.lifespan` as an ``async with`` context manager
  (already an ``@asynccontextmanager``).
- ``monkeypatch.setattr`` on every deferred-import target the lifespan
  resolves at call time: ``app.config.require_prod_secrets``,
  ``app.config.validate_config``, ``app.config.get_config``,
  ``storage.postgres.init_db``, ``storage.postgres.close_pool``,
  ``storage.redis.close_client``, ``app.discord.bot.start_bot``,
  ``app.discord.bot.get_bot``, ``app.discord.bot.get_notifier``.
- Stub ``cfg.discord_bot_token`` empty by default so the bot task is
  NOT launched in non-bot tests; only ``test_bot_started_and_stopped``
  sets the token to a truthy value.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI


def _stub_cfg(token: str = "") -> MagicMock:
    """Build a MagicMock cfg with empty defaults + the given bot token."""
    cfg = MagicMock()
    cfg.discord_bot_token = token
    cfg.discord_bot_configured = bool(token)
    cfg.discord_app_id_valid = False
    cfg.api_key_configured = False
    cfg.api_owner_configured = False
    return cfg


@pytest.fixture
def lifespan_deps(monkeypatch):
    """Wire up monkeypatched deps the lifespan reads at call time.

    Yields a dict of references so individual tests can re-monkeypatch
    specific ones (e.g. to inject a raise).
    """
    deps = {
        "require_prod_secrets": MagicMock(),
        "validate_config": MagicMock(),
        "cfg": _stub_cfg(),
        "init_db": AsyncMock(),
        "close_pool": AsyncMock(),
        "close_client": AsyncMock(),
        "start_bot": AsyncMock(),
    }
    monkeypatch.setattr("app.config.require_prod_secrets", deps["require_prod_secrets"])
    monkeypatch.setattr("app.config.validate_config", deps["validate_config"])
    monkeypatch.setattr("app.config.get_config", MagicMock(return_value=deps["cfg"]))
    monkeypatch.setattr("storage.postgres.init_db", deps["init_db"])
    monkeypatch.setattr("storage.postgres.close_pool", deps["close_pool"])
    monkeypatch.setattr("storage.redis.close_client", deps["close_client"])
    monkeypatch.setattr("app.discord.bot.start_bot", deps["start_bot"])
    return deps


class TestLifespanStartupShutdown:
    """Happy path + validate_config / init_db / bot-lifecycle branches."""

    async def test_happy_path_dev_env_no_bot_token(self, lifespan_deps):
        """Dev env + empty DISCORD_BOT_TOKEN → bot task never created.

        Verifies init_db called once at startup; close_client +
        close_pool each called once at shutdown; start_bot never
        invoked (token empty → ``if cfg.discord_bot_token`` False).
        """
        from app.main import lifespan

        app = FastAPI()
        async with lifespan(app):
            lifespan_deps["init_db"].assert_awaited_once()
            lifespan_deps["start_bot"].assert_not_called()

        lifespan_deps["close_client"].assert_awaited_once()
        lifespan_deps["close_pool"].assert_awaited_once()

    async def test_validate_config_raises_propagates(self, lifespan_deps, monkeypatch):
        """``validate_config`` raises → lifespan re-raises wrapped with
        ``Configuration validation failed - cannot start`` and the
        original error preserved as ``__cause__`` (app/main.py:38-40).
        init_db must NOT be called after a validate_config failure.
        """
        from app.main import lifespan

        lifespan_deps["validate_config"].side_effect = RuntimeError("bad max_t2")
        app = FastAPI()
        with pytest.raises(RuntimeError) as exc_info:
            async with lifespan(app):
                pytest.fail("lifespan must not yield when validate_config fails")
        assert "Configuration validation failed" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        lifespan_deps["init_db"].assert_not_called()

    async def test_init_db_failure_wraps_in_runtime_error(self, lifespan_deps):
        """``init_db`` raises → lifespan catches Exception and re-raises
        as ``Database initialization failed - cannot start`` with the
        original exception as ``__cause__`` (app/main.py:42-47).
        """
        from app.main import lifespan

        lifespan_deps["init_db"].side_effect = Exception("connection refused")
        app = FastAPI()
        with pytest.raises(RuntimeError) as exc_info:
            async with lifespan(app):
                pytest.fail("lifespan must not yield when init_db fails")
        assert "Database initialization failed" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, Exception)

    async def test_bot_started_and_stopped_with_notifier(self, monkeypatch):
        """Truthy DISCORD_BOT_TOKEN → ``start_bot`` launches as a task;
        on shutdown, ``notifier.stop()`` and ``bot.close()`` are awaited
        before the always-run close_client / close_pool cleanup.

        Ordering matters: notifier must drain before bot closes its HTTP
        session (app/main.py:156-172).
        """
        from app.main import lifespan

        cfg = _stub_cfg(token="test-token-not-empty")

        async def fake_start() -> None:
            # Yield once so the lifespan's create_task line continues.
            await asyncio.sleep(0)

        bot = MagicMock()
        bot.close = AsyncMock()
        notifier = MagicMock()
        notifier.stop = AsyncMock()

        monkeypatch.setattr("app.config.require_prod_secrets", MagicMock())
        monkeypatch.setattr("app.config.validate_config", MagicMock())
        monkeypatch.setattr("app.config.get_config", MagicMock(return_value=cfg))
        monkeypatch.setattr("storage.postgres.init_db", AsyncMock())
        monkeypatch.setattr("storage.postgres.close_pool", AsyncMock())
        monkeypatch.setattr("storage.redis.close_client", AsyncMock())
        monkeypatch.setattr("app.discord.bot.start_bot", fake_start)
        monkeypatch.setattr("app.discord.bot.get_bot", MagicMock(return_value=bot))
        monkeypatch.setattr("app.discord.bot.get_notifier", MagicMock(return_value=notifier))

        app = FastAPI()
        async with lifespan(app):
            pass

        notifier.stop.assert_awaited()
        bot.close.assert_awaited()

    @pytest.mark.parametrize(
        "failing_cleanup",
        ["close_pool", "close_client"],
    )
    async def test_shutdown_swallows_cleanup_failures(
        self, monkeypatch, failing_cleanup
    ):
        """Either ``close_pool`` or ``close_client`` raising must NOT
        prevent the OTHER cleanup from running — both are guarded by
        ``try / except`` in app/main.py:174-184.

        Parametrize the failing one so a future refactor that breaks
        one branch but not the other still surfaces the gap.
        """
        from app.main import lifespan

        cfg = _stub_cfg()
        close_pool = AsyncMock()
        close_client = AsyncMock()
        if failing_cleanup == "close_pool":
            close_pool.side_effect = Exception("pool teardown failed")
        else:
            close_client.side_effect = Exception("redis teardown failed")

        monkeypatch.setattr("app.config.require_prod_secrets", MagicMock())
        monkeypatch.setattr("app.config.validate_config", MagicMock())
        monkeypatch.setattr("app.config.get_config", MagicMock(return_value=cfg))
        monkeypatch.setattr("storage.postgres.init_db", AsyncMock())
        monkeypatch.setattr("storage.postgres.close_pool", close_pool)
        monkeypatch.setattr("storage.redis.close_client", close_client)

        app = FastAPI()
        async with lifespan(app):
            pass

        # Both cleanups ran regardless of which one raised.
        close_pool.assert_awaited_once()
        close_client.assert_awaited_once()