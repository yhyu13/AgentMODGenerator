"""HTTP-layer 422 validation tests for ``POST /v1/mods/generate``.

Complements ``tests/test_generate_mod_endpoint.py`` (v149). The v149
file pins the *happy path* contract (request_id shape, status
field, the create → set_status → bg ordering pin). This file pins
the 422 contract — FastAPI's standard Pydantic ValidationError
response — for the malformed-body cases.

422 surface driven by ``app/api/schemas.py::GenerateRequest``:
  - ``user_id: str``              — required, no default
  - ``prompt: str``               — required, ``Field(max_length=10000)``
  - ``phase: str | None = None``  — optional, no validator

Cases below: missing ``prompt``, missing ``user_id``, empty body
(both missing), ``prompt`` over 10000 chars, ``prompt`` as int.

Each test pins THREE things:
  1. ``status_code == 422``.
  2. The response ``detail`` list contains an entry whose ``loc``
     tuple points at the offending field with the expected Pydantic
     error type prefix.
  3. NONE of the orchestration side effects fire — neither
     ``create_mod_request``, ``redis_set_status``, nor
     ``run_pipeline_background``. The handler must reject the
     body BEFORE the orchestration chain starts. Without this
     pin a refactor that validates *after* kicking off the
     pipeline would pass the 422 assertion but waste DB / Redis
     writes.

Re-uses v149's ``_StubbedDeps`` helper shape (re-declared locally
so this file is self-contained — a regression in one helper
should not silently break the other file).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _StubbedDeps:
    """Same triple-mock surface as ``tests/test_generate_mod_endpoint.py``."""

    def __init__(self) -> None:
        self.create = AsyncMock()
        self.set_status = AsyncMock()
        self.bg = MagicMock()

    def __enter__(self) -> "_StubbedDeps":
        self._mp = pytest.MonkeyPatch()
        self._mp.setattr("storage.queries.create_mod_request", self.create)
        self._mp.setattr("storage.redis.set_status", self.set_status)
        self._mp.setattr("orchestrator.pipeline.run_pipeline_background", self.bg)
        return self

    def __exit__(self, *args: object) -> None:
        self._mp.undo()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _assert_422(
    client: TestClient, deps: _StubbedDeps, body: object,
    *, expected_loc_field: str, expected_type_prefix: str,
) -> None:
    """Send ``body`` and assert 422 + correct field + zero side effects."""
    r = client.post("/v1/mods/generate", json=body)
    assert r.status_code == 422, (
        f"expected 422 for body={body!r}, got {r.status_code}: {r.text!r}"
    )
    detail = r.json().get("detail")
    assert isinstance(detail, list) and detail, (
        f"expected non-empty list in detail, got {detail!r}"
    )
    match = next(
        (e for e in detail
         if e.get("loc", [None, None])[-1] == expected_loc_field
         and e.get("type", "").startswith(expected_type_prefix)),
        None,
    )
    assert match is not None, (
        f"no error pinned to {expected_loc_field!r} with type starting "
        f"{expected_type_prefix!r}; detail={detail!r}"
    )
    # Pin: 422 must reject the body BEFORE any orchestration side effect.
    for name, mock, attr in (
        ("create_mod_request", deps.create, "await_count"),
        ("redis set_status", deps.set_status, "await_count"),
        ("run_pipeline_background", deps.bg, "call_count"),
    ):
        count = getattr(mock, attr)
        assert count == 0, f"{name} fired despite 422 ({attr}={count})"


class TestGenerateModEndpointValidation:
    """Pin the 422 contract of ``POST /v1/mods/generate``."""

    def test_missing_prompt_returns_422(self, client: TestClient) -> None:
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"user_id": "u1"},  # no prompt
                expected_loc_field="prompt",
                expected_type_prefix="missing",
            )

    def test_missing_user_id_returns_422(self, client: TestClient) -> None:
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"prompt": "make a texture mod"},  # no user_id
                expected_loc_field="user_id",
                expected_type_prefix="missing",
            )

    def test_missing_both_required_fields_returns_422(
        self, client: TestClient,
    ) -> None:
        """Empty body — both ``user_id`` and ``prompt`` are missing.

        FastAPI returns BOTH errors in ``detail`` (one entry per
        missing required field). We pin that both fields appear.
        """
        with _StubbedDeps() as deps:
            r = client.post("/v1/mods/generate", json={})
        assert r.status_code == 422
        detail = r.json()["detail"]
        locs = {e.get("loc", [None])[-1] for e in detail}
        assert "user_id" in locs, (
            f"no 'user_id' missing-field error in detail={detail!r}"
        )
        assert "prompt" in locs, (
            f"no 'prompt' missing-field error in detail={detail!r}"
        )
        # Same side-effect pin as the single-missing case.
        assert deps.create.await_count == 0
        assert deps.set_status.await_count == 0
        assert deps.bg.call_count == 0

    def test_oversized_prompt_returns_422(self, client: TestClient) -> None:
        """``prompt`` over ``Field(max_length=10000)`` triggers ``string_too_long``.

        The happy-path tests in v149 implicitly pin the 10000-char
        boundary by using short prompts.
        """
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"user_id": "u1", "prompt": "x" * 10_001},
                expected_loc_field="prompt",
                expected_type_prefix="string_too_long",
            )

    def test_wrong_type_for_prompt_returns_422(self, client: TestClient) -> None:
        """``prompt`` must be a string; sending an int is a 422.

        Accepts both ``string_type`` and ``type_error`` Pydantic
        type-name prefixes so the test is stable across Pydantic
        minor versions where the suffix can drift.
        """
        with _StubbedDeps() as deps:
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u1", "prompt": 12345},
            )
        assert r.status_code == 422
        detail = r.json()["detail"]
        match = next(
            (e for e in detail
             if e.get("loc", [None, None])[-1] == "prompt"
             and ("string_type" in e.get("type", "")
                  or e.get("type", "").startswith("type_error"))),
            None,
        )
        assert match is not None, (
            f"no string-type error pinned to prompt; detail={detail!r}"
        )
        assert deps.create.await_count == 0
        assert deps.set_status.await_count == 0
        assert deps.bg.call_count == 0