"""Tests for ``GET /v1/mods/{request_id}/metadata``.

Round v49: ports the route handler from the discord-ops-hardening
branch (source: ``docs/_source_routes_app_api.py.txt`` lines
2386-2442). The endpoint reads ``metadata.json`` and ``version.json``
from the packaged zip on disk via ``generators.packager.read_zip``.

The handler's contract:

- 200 with a ``ModMetadataResponse``-shaped JSON body when the
  request exists. ``metadata`` and ``version`` are dicts that come
  from ``json.loads`` of the packaged zip's two JSON files (or empty
  dicts when those files are absent).
- 404 when the request id is unknown (no row in ``mod_outputs``).
- 200 with empty dicts when the request exists but isn't packaged
  yet (``zip_key`` is None).
- 500 when ``read_zip`` raises ``ValueError`` (zip_key validation
  failure) or ``OSError`` (filesystem error reading the zip).
- Per-file graceful degrade: a single corrupt ``metadata.json`` or
  ``version.json`` is logged at WARNING and the corresponding field
  falls back to an empty dict — the other field still loads.

Tests use ``monkeypatch.setattr`` on the module-level
``app.api.routes.get_mod_output`` and on
``generators.packager.read_zip`` (imported inside the handler, so
the patch target is ``generators.packager.read_zip``).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.api.routes as routes_module
import generators.packager as packager_module
from app.api.routes import get_mod_metadata
from app.api.schemas import ModMetadataResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_METADATA = {
    "Format": "1.29.0",
    "UniqueID": "tv_shopping_network",
    "Name": "TV Shopping Network",
    "Author": "AI Generator",
    "Version": "1.0.0",
}

SAMPLE_VERSION = {
    "schema_version": 1,
    "generator_build": "2026-07-04",
    "generator_revision": "abc1234",
}


def _patch_query(output):
    """Patch ``app.api.routes.get_mod_output`` to return ``output``.

    The handler imports ``get_mod_output`` at module top-level
    (``from storage.queries import get_mod_output``), so patching
    the module-level name is enough. Returns the patch context
    manager and the AsyncMock so callers can assert the helper was
    called once with the right request_id.
    """
    mock = AsyncMock(return_value=output)
    return patch.object(routes_module, "get_mod_output", mock), mock


def _patch_read_zip(zip_files, side_effect=None):
    """Patch ``generators.packager.read_zip``.

    The handler does ``from generators.packager import read_zip``
    inside the function body, so the local name binding is fresh on
    every call. Patching the canonical attribute on the source module
    is enough. ``read_zip`` is a sync function, so we use a plain
    ``MagicMock`` (``side_effect`` lets us raise from it).
    """
    mock = MagicMock(return_value=zip_files, side_effect=side_effect)
    return patch.object(packager_module, "read_zip", mock), mock


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestMetadataEndpoint:
    """Tests for GET /v1/mods/{request_id}/metadata."""

    async def test_returns_parsed_metadata_and_version(self):
        """200 with both metadata and version populated from the zip."""
        zip_files = {
            "metadata.json": json.dumps(SAMPLE_METADATA),
            "version.json": json.dumps(SAMPLE_VERSION),
        }
        rp_ctx, _ = _patch_read_zip(zip_files)
        q_ctx, query_mock = _patch_query({"zip_key": "req-1.zip"})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-1")

        assert isinstance(result, ModMetadataResponse)
        assert result.request_id == "req-1"
        assert result.metadata == SAMPLE_METADATA
        assert result.version == SAMPLE_VERSION
        query_mock.assert_awaited_once_with("req-1")

    async def test_returns_empty_dicts_when_files_missing(self):
        """200 with empty dicts when zip has neither metadata.json nor version.json."""
        rp_ctx, _ = _patch_read_zip({"unrelated.txt": "hello"})
        q_ctx, _ = _patch_query({"zip_key": "req-empty.zip"})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-empty")

        assert result.request_id == "req-empty"
        assert result.metadata == {}
        assert result.version == {}

    async def test_returns_empty_dicts_when_version_only_missing(self):
        """Older zips may have metadata.json but no version.json — handle that."""
        zip_files = {"metadata.json": json.dumps(SAMPLE_METADATA)}
        rp_ctx, _ = _patch_read_zip(zip_files)
        q_ctx, _ = _patch_query({"zip_key": "req-old.zip"})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-old")

        assert result.metadata == SAMPLE_METADATA
        assert result.version == {}

    async def test_returns_empty_dicts_when_request_not_yet_packaged(self):
        """Request exists in DB but zip_key is None — return empty, NOT 404.

        The source distinguishes "no row in mod_outputs" (404) from
        "row exists but no zip yet" (200 + empty). This test exercises
        the second case.
        """
        # read_zip must NOT be called in this path; we still patch it
        # so a regression that DOES call it doesn't raise.
        rp_ctx, _ = _patch_read_zip({})
        q_ctx, query_mock = _patch_query({"zip_key": None})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-running")

        assert result.request_id == "req-running"
        assert result.metadata == {}
        assert result.version == {}
        query_mock.assert_awaited_once_with("req-running")

    # ---------------------------------------------------------------------
    # Failure paths
    # ---------------------------------------------------------------------

    async def test_returns_404_when_request_not_found(self):
        """No row in mod_outputs → 404."""
        q_ctx, _ = _patch_query(None)
        with q_ctx:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_metadata("req-missing")

        assert exc_info.value.status_code == 404
        assert "req-missing" in exc_info.value.detail

    async def test_returns_500_when_read_zip_raises_value_error(self):
        """``read_zip`` raises ``ValueError`` on invalid zip_key → 500."""
        rp_ctx, _ = _patch_read_zip(
            {},
            side_effect=ValueError("Invalid zip_key format: 'bad/../key'"),
        )
        q_ctx, _ = _patch_query({"zip_key": "bad/../key"})
        with rp_ctx, q_ctx:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_metadata("req-bad-key")

        assert exc_info.value.status_code == 500
        assert "Failed to read packaged zip" in exc_info.value.detail

    async def test_returns_500_when_read_zip_raises_os_error(self):
        """``read_zip`` raises ``OSError`` on missing zip file → 500."""
        rp_ctx, _ = _patch_read_zip(
            {},
            side_effect=OSError("No such file or directory"),
        )
        q_ctx, _ = _patch_query({"zip_key": "req-ghost.zip"})
        with rp_ctx, q_ctx:
            with pytest.raises(HTTPException) as exc_info:
                await get_mod_metadata("req-ghost")

        assert exc_info.value.status_code == 500

    # ---------------------------------------------------------------------
    # Graceful-degrade: per-file JSON parse failure
    # ---------------------------------------------------------------------

    async def test_invalid_metadata_json_falls_back_to_empty_dict(self):
        """Corrupt ``metadata.json`` is logged at WARNING; the field
        falls back to an empty dict but the other field still loads."""
        zip_files = {
            "metadata.json": "{not valid json",
            "version.json": json.dumps(SAMPLE_VERSION),
        }
        rp_ctx, _ = _patch_read_zip(zip_files)
        q_ctx, _ = _patch_query({"zip_key": "req-bad-meta.zip"})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-bad-meta")

        assert result.metadata == {}
        assert result.version == SAMPLE_VERSION

    async def test_invalid_version_json_falls_back_to_empty_dict(self):
        """Corrupt ``version.json`` is logged at WARNING; the field
        falls back to an empty dict but ``metadata`` still loads."""
        zip_files = {
            "metadata.json": json.dumps(SAMPLE_METADATA),
            "version.json": "{broken",
        }
        rp_ctx, _ = _patch_read_zip(zip_files)
        q_ctx, _ = _patch_query({"zip_key": "req-bad-ver.zip"})
        with rp_ctx, q_ctx:
            result = await get_mod_metadata("req-bad-ver")

        assert result.metadata == SAMPLE_METADATA
        assert result.version == {}


# ---------------------------------------------------------------------------
# Schema-level tests
# ---------------------------------------------------------------------------


class TestModMetadataResponseSchema:
    """Schema-level tests for ``ModMetadataResponse``.

    These don't need any storage or zip mocking — they just verify the
    response model's contract independently of the handler.
    """

    def test_default_dicts(self):
        """Both metadata and version default to empty dicts."""
        resp = ModMetadataResponse(request_id="req-x")
        assert resp.request_id == "req-x"
        assert resp.metadata == {}
        assert resp.version == {}

    def test_explicit_dicts_round_trip(self):
        """Explicit dicts survive serialization."""
        resp = ModMetadataResponse(
            request_id="req-y",
            metadata={"key": "value"},
            version={"build": "abc"},
        )
        dumped = resp.model_dump()
        assert dumped["request_id"] == "req-y"
        assert dumped["metadata"] == {"key": "value"}
        assert dumped["version"] == {"build": "abc"}

    def test_mutable_default_isolation(self):
        """Two default-constructed responses must not share the same dict.

        Pydantic v2's ``default_factory`` handles this, but it's a
        classic bug to verify — a bare ``= {}`` default would share
        one mutable dict across all instances and corrupt subsequent
        responses on mutation.
        """
        a = ModMetadataResponse(request_id="a")
        b = ModMetadataResponse(request_id="b")
        assert a.metadata is not b.metadata
        assert a.version is not b.version