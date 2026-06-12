"""Tests for health probes."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.health import _probe


class TestProbe:
    @pytest.mark.asyncio
    async def test_probe_success(self):
        async def success_coro():
            return "ok"

        result = await _probe("test", success_coro, timeout=1.0)
        assert result["name"] == "test"
        assert result["ok"] is True
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_probe_failure(self):
        async def fail_coro():
            raise RuntimeError("connection refused")

        result = await _probe("test", fail_coro, timeout=1.0)
        assert result["name"] == "test"
        assert result["ok"] is False
        assert "error" in result
        assert "RuntimeError" in result["error"]

    @pytest.mark.asyncio
    async def test_probe_timeout(self):
        async def slow_coro():
            await asyncio.sleep(10)

        result = await _probe("test", slow_coro, timeout=0.1)
        assert result["name"] == "test"
        assert result["ok"] is False
        assert "TimeoutError" in result["error"]

    @pytest.mark.asyncio
    async def test_probe_latency_recorded(self):
        async def success_coro():
            await asyncio.sleep(0.05)
            return "ok"

        result = await _probe("test", success_coro, timeout=1.0)
        assert result["latency_ms"] >= 50
