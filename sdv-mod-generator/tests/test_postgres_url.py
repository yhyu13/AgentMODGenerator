"""Tests for the per-call ``_database_url()`` helper in storage/postgres.py.

Pins the v74 Blue testability fix: ``DATABASE_URL`` is now read on
every call (not snapshotted at module import time) so a test that
``monkeypatch.setenv("DATABASE_URL", ...)`` sees the new value on
the very next ``get_engine()`` invocation — without having to
``importlib.reload()`` the module.

Also pins ``_reset_engine_for_tests()``, the test-only hook that
clears the cached engine so the next ``get_engine()`` rebuilds with
the new URL.

The original bug: ``_DATABASE_URL = os.getenv(...)`` at module
top snapped the value at import time. Tests running after a
``monkeypatch.setenv`` would still construct an engine from the
import-time URL — the patch was silently ignored.

Reference: ``docs/_source_postgres.py.txt`` lines 31-53 (the
upstream v74 Blue implementation we are mirroring).
"""
from __future__ import annotations

import importlib

import pytest

import storage.postgres as postgres_module
from storage.postgres import _database_url, _reset_engine_for_tests


class TestDatabaseUrlHelper:
    """Direct tests for the standalone ``_database_url()`` helper."""

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No DATABASE_URL → the docker-compose dev default."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _database_url() == "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods"

    def test_returns_env_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL set → helper echoes it through, no transformation."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://prod-host:5432/sdv_mods")
        assert _database_url() == "postgresql+asyncpg://prod-host:5432/sdv_mods"

    def test_reads_per_call_not_at_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact bug: set/unset/set between calls must be observed.

        With the legacy ``_DATABASE_URL = os.getenv(...)`` module
        constant the second ``_database_url()`` call would still see
        the import-time value. With the v74 Blue per-call helper
        each ``_database_url()`` invocation re-reads the env.
        """
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _database_url() == "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods"

        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://staging:5432/x")
        assert _database_url() == "postgresql+asyncpg://staging:5432/x"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _database_url() == "postgresql+asyncpg://postgres:postgres@localhost:5432/sdv_mods"

    def test_empty_env_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL="" is preserved as "" — matches ``os.getenv`` semantics.

        A caller that wants to opt out of the default can do so
        cleanly by setting the env to the empty string. The helper
        does not coalesce that to the default.
        """
        monkeypatch.setenv("DATABASE_URL", "")
        assert _database_url() == ""


class TestResetEngineForTests:
    """The test-only ``_reset_engine_for_tests()`` hook must clear the cached engine."""

    def teardown_method(self) -> None:
        # Always reset after each test, even on failure, so test
        # order never leaks an engine from one case to the next.
        _reset_engine_for_tests()

    def test_clears_cached_engine(self) -> None:
        """After the helper runs, ``_engine`` is ``None`` and ``_session_factory`` is ``None``."""
        # Pretend something cached an engine + factory.
        postgres_module._engine = object()  # type: ignore[assignment]
        postgres_module._session_factory = object()  # type: ignore[assignment]

        _reset_engine_for_tests()

        assert postgres_module._engine is None
        assert postgres_module._session_factory is None

    def test_idempotent_when_already_clear(self) -> None:
        """Calling twice in a row is a no-op the second time."""
        # State already cleared.
        assert postgres_module._engine is None

        _reset_engine_for_tests()
        _reset_engine_for_tests()

        assert postgres_module._engine is None
        assert postgres_module._session_factory is None


class TestModuleImportOrder:
    """Pin that ``storage.postgres`` reads ``DATABASE_URL`` lazily.

    Importing the module must NOT consume ``DATABASE_URL`` into a
    cached global — the env value can change after import and the
    helper must observe the new value.
    """

    def test_import_does_not_cache_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Force a fresh import — already-imported modules keep a
        # stale global if the source had one. With the helper in
        # place there is no global to cache, but we still verify
        # the new value is observed.
        importlib.reload(postgres_module)
        try:
            monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://after-import:5432/y")
            from storage.postgres import _database_url as fresh
            assert fresh() == "postgresql+asyncpg://after-import:5432/y"
        finally:
            importlib.reload(postgres_module)
