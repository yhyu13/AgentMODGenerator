"""Tests for ``storage.redis.get_pipeline_logs`` (read-side helper).

Round v118: closes the read-side storage-helper test gap on the
v75 pipeline-log triad. Companion to v116 (writer side,
``append_pipeline_log``) and v117 (route handler, the endpoint).
The function under test is the third leg of the triad — the
storage-layer read that the route handler wraps. We pin the
contract the handler depends on: ``[]`` (NOT ``None``) on key
miss, ``limit`` clamped to ``[1, _PIPELINE_LOG_MAX_ENTRIES]``,
``limit <= 0`` → ``[]`` with no ``lrange`` call, malformed JSON
and non-dict entries silently skipped, no raise on bad data.

The function calls ``await get_client(); client.lrange(...)``.
We monkeypatch ``storage.redis.get_client`` with a tiny
``_FakeClient`` whose ``lrange`` is the configurable surface —
mirrors the v68 + v69 + v117 convention.
"""
from __future__ import annotations

import json

import pytest

from storage.redis import (
    _PIPELINE_LOG_MAX_ENTRIES,
    get_pipeline_logs,
)


class _FakeClient:
    """Tiny stand-in for ``redis.asyncio.Redis`` exposing just ``lrange``."""

    def __init__(self, lrange_value):
        self._lrange_value = lrange_value
        self.lrange_calls: list[tuple[str, int, int]] = []

    async def lrange(self, key: str, start: int, stop: int):
        self.lrange_calls.append((key, start, stop))
        return self._lrange_value


def _patch_redis_client(monkeypatch, fake: _FakeClient) -> None:
    """Bind ``storage.redis.get_client`` to return ``fake`` for this test."""

    async def _stub_get_client():
        return fake

    monkeypatch.setattr("storage.redis.get_client", _stub_get_client)


def _make_entry(event: str, level: str = "INFO", message: str = "msg") -> dict:
    """Well-formed log-entry dict matching ``append_pipeline_log``."""
    return {
        "timestamp": "2026-07-09T12:00:00+00:00",
        "level": level,
        "event": event,
        "message": message,
    }


class TestGetPipelineLogsHappyPath:
    """Redis-hit path with well-formed entries."""

    async def test_returns_entries_in_lrange_order(self, monkeypatch):
        """A 2-entry Redis list is returned in the order ``lrange``
        produced them (newest-first because ``append_pipeline_log``
        uses ``LPUSH``). Pinned because ``_build_log_entries`` and
        ``ModLogsResponse`` render the list as-is.
        """
        e1 = _make_entry("first", level="INFO")
        e2 = _make_entry("second", level="WARNING")
        entries = [json.dumps(e1), json.dumps(e2)]
        fake = _FakeClient(entries)
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-1", limit=10)
        assert result == [e1, e2]

    async def test_default_limit_is_100(self, monkeypatch):
        """Default ``limit=100`` makes the lrange stop bound 99."""
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        await get_pipeline_logs("req-default-1")
        assert fake.lrange_calls == [("pipeline:logs:req-default-1", 0, 99)]

    async def test_request_id_is_included_in_key(self, monkeypatch):
        """Redis key includes the request_id verbatim — pinned so
        a future refactor that strips/lower-cases/hashes the key
        surfaces here.
        """
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        await get_pipeline_logs("req-with-DASH-and-UNDERSCORE_99")
        assert fake.lrange_calls[0][0] == (
            "pipeline:logs:req-with-DASH-and-UNDERSCORE_99"
        )

    async def test_limit_passed_through_to_lrange(self, monkeypatch):
        """``limit`` drives the lrange stop bound (``stop = limit - 1``)."""
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        await get_pipeline_logs("req-limit-1", limit=42)
        assert fake.lrange_calls == [("pipeline:logs:req-limit-1", 0, 41)]


class TestGetPipelineLogsEmptyLimit:
    """``limit <= 0`` short-circuit (no Redis round-trip)."""

    @pytest.mark.parametrize("bad_limit", [0, -1, -5])
    async def test_limit_le_zero_returns_empty_without_lrange(
        self, monkeypatch, bad_limit
    ):
        """``limit <= 0`` → ``[]`` and NO ``lrange`` call."""
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-empty-limit", limit=bad_limit)
        assert result == []
        assert fake.lrange_calls == []


class TestGetPipelineLogsEmptyKey:
    """Redis-miss / empty-list path."""

    async def test_empty_redis_returns_empty_list(self, monkeypatch):
        """``lrange`` returns ``[]`` → handler returns ``[]`` (NOT ``None``).

        The route handler checks ``len(raw_entries) > 0`` to
        decide whether to fall through to the DB existence check,
        so the contract here is critical: an empty list is the
        "key missing" signal, distinct from ``None`` which would
        blow up ``len()`` in the handler.
        """
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-miss-1")
        assert result == []
        assert result is not None


class TestGetPipelineLogsLimitClamping:
    """``limit`` → ``[1, _PIPELINE_LOG_MAX_ENTRIES]`` clamp."""

    async def test_limit_clamped_above_cap(self, monkeypatch):
        """``limit=1000`` is clamped to ``_PIPELINE_LOG_MAX_ENTRIES - 1``."""
        fake = _FakeClient([])
        _patch_redis_client(monkeypatch, fake)

        await get_pipeline_logs("req-clamp-high", limit=1000)
        stop = fake.lrange_calls[0][2]
        assert stop == _PIPELINE_LOG_MAX_ENTRIES - 1
        assert _PIPELINE_LOG_MAX_ENTRIES == 500

    async def test_limit_clamped_below_one(self, monkeypatch):
        """``limit=1`` is honored exactly (stop = 0, single entry)."""
        fake = _FakeClient([json.dumps(_make_entry("e1"))])
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-clamp-low", limit=1)
        assert fake.lrange_calls == [("pipeline:logs:req-clamp-low", 0, 0)]
        assert len(result) == 1


class TestGetPipelineLogsMalformedEntries:
    """Defensive handling of bad Redis entries."""

    async def test_malformed_json_entry_is_skipped(self, monkeypatch):
        """Non-JSON entry silently dropped, remaining entries returned."""
        good1 = _make_entry("good")
        good2 = _make_entry("good2")
        entries = [json.dumps(good1), "not-valid-json{", json.dumps(good2)]
        fake = _FakeClient(entries)
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-malformed-1")
        assert result == [good1, good2]

    async def test_non_dict_entry_is_skipped(self, monkeypatch):
        """Non-dict entry (e.g. JSON ``null`` → ``None``) silently dropped."""
        good = _make_entry("good")
        entries = [json.dumps(good), json.dumps(None)]
        fake = _FakeClient(entries)
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-non-dict-1")
        assert result == [good]

    async def test_all_malformed_returns_empty_list(self, monkeypatch):
        """All-bad input → ``[]`` (NOT ``None``, NOT a raise).

        The storage layer collapses "key missing" and "all entries
        bad" into the same empty-list return so the route handler
        falls through to the DB existence check in both cases.
        """
        entries = ["not-json", "{also-not-json", "[1,2,3]"]
        fake = _FakeClient(entries)
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-all-bad-1")
        assert result == []

    async def test_valid_entries_surrounded_by_bad_are_kept(self, monkeypatch):
        """Bad entry in the middle does not affect surrounding valid entries.

        Pinned so a future refactor that uses ``any()`` or
        ``all()`` (and short-circuits on the first bad value)
        surfaces here.
        """
        good1 = _make_entry("a")
        good2 = _make_entry("b", level="WARNING", message="second")
        entries = [json.dumps(good1), "{not-valid-json", json.dumps(good2)]
        fake = _FakeClient(entries)
        _patch_redis_client(monkeypatch, fake)

        result = await get_pipeline_logs("req-mixed-1")
        assert result == [good1, good2]