"""HTTP-layer 422 validation tests for ``POST /v1/mods/generate/batch``.

Complements ``tests/test_generate_mod_batch.py`` (v68 — happy path
contract: batch_id shape, per-item uniqueness, create → set_status
→ bg ordering, per-item ``_estimate_seconds`` mapping) and the
pre-existing schema-level ``tests/test_batch_api.py`` (which raises
``pydantic.ValidationError`` on direct construction but does NOT
exercise the FastAPI HTTP layer's 422 response shape).

This file pins the 422 contract that FastAPI produces when the
``BatchGenerateRequest`` schema rejects a malformed body:

  - ``user_id: str``                              — required, no default
  - ``prompts: list[str] = Field(min_length=1,
                                  max_length=10)`` — required, no default
  - ``phase: str | None = None``                  — optional, no validator

Cases below: missing ``prompts``, empty ``prompts`` list, ``prompts``
list over the 10-cap, missing ``user_id``, ``prompts`` as a string
instead of a list.

Each test pins THREE things:

  1. ``status_code == 422``.
  2. The response ``detail`` list contains an entry whose ``loc``
     tuple points at the offending field with the expected
     Pydantic error type prefix. We accept BOTH the ``too_short``
     and the ``min_length`` (and ``too_long`` / ``max_length``)
     prefixes so the test is stable across Pydantic minor versions
     where the suffix can drift.
  3. NONE of the orchestration side effects fire — neither
     ``create_mod_request``, ``redis_set_status``, nor
     ``run_pipeline_background``. The handler must reject the
     body BEFORE the per-prompt loop starts. Without this pin
     a refactor that validates *after* entering the loop would
     pass the 422 assertion but waste DB / Redis writes.

Re-uses v150's ``_StubbedDeps`` helper shape (re-declared locally
so this file is self-contained — a regression in one helper
should not silently break the other file).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _StubbedDeps:
    """Same triple-mock surface as ``tests/test_generate_mod_validation.py``."""

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
    *, expected_loc_field: str, expected_type_prefixes: tuple[str, ...],
) -> None:
    """Send ``body`` and assert 422 + correct field + zero side effects.

    ``expected_type_prefixes`` is a tuple of acceptable Pydantic error
    type-name prefixes. We accept multiple so the test is stable across
    Pydantic minor versions where the suffix can drift (e.g. ``too_short``
    vs ``min_length`` for ``Field(min_length=1)`` on a list).
    """
    r = client.post("/v1/mods/generate/batch", json=body)
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
         and any(
             e.get("type", "").startswith(p) for p in expected_type_prefixes
         )),
        None,
    )
    assert match is not None, (
        f"no error pinned to {expected_loc_field!r} with type starting "
        f"any of {expected_type_prefixes!r}; detail={detail!r}"
    )
    # Pin: 422 must reject the body BEFORE any orchestration side effect.
    for name, mock, attr in (
        ("create_mod_request", deps.create, "await_count"),
        ("redis set_status", deps.set_status, "await_count"),
        ("run_pipeline_background", deps.bg, "call_count"),
    ):
        count = getattr(mock, attr)
        assert count == 0, f"{name} fired despite 422 ({attr}={count})"


class TestGenerateModBatchEndpointValidation:
    """Pin the 422 contract of ``POST /v1/mods/generate/batch``."""

    def test_missing_prompts_returns_422(self, client: TestClient) -> None:
        """``prompts`` is a required field; omitting it is a 422."""
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"user_id": "u1"},  # no prompts
                expected_loc_field="prompts",
                expected_type_prefixes=("missing",),
            )

    def test_empty_prompts_list_returns_422(self, client: TestClient) -> None:
        """``Field(min_length=1)`` rejects an empty ``prompts`` list.

        The handler's per-prompt loop would otherwise iterate zero
        times and return a batch with zero items — a silently-broken
        contract from the client's perspective. The schema surfaces
        it as a 422.
        """
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"user_id": "u1", "prompts": []},
                expected_loc_field="prompts",
                # Pydantic v1/v2 emit "too_short"; some versions emit
                # "min_length" — accept both.
                expected_type_prefixes=("too_short", "min_length"),
            )

    def test_too_many_prompts_returns_422(self, client: TestClient) -> None:
        """``Field(max_length=10)`` rejects 11+ prompts.

        Without the schema-level cap, a single client could submit
        10,000 prompts and the orchestrator would fan out 10,000
        background tasks. The 10-cap is a load-bearing DoS guard.
        """
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"user_id": "u1", "prompts": [f"p{i}" for i in range(11)]},
                expected_loc_field="prompts",
                # Same belt-and-suspenders as the empty-list case.
                expected_type_prefixes=("too_long", "max_length"),
            )

    def test_missing_user_id_returns_422(self, client: TestClient) -> None:
        """``user_id`` is a required field; omitting it is a 422."""
        with _StubbedDeps() as deps:
            _assert_422(
                client, deps,
                body={"prompts": ["make a texture mod"]},  # no user_id
                expected_loc_field="user_id",
                expected_type_prefixes=("missing",),
            )

    def test_wrong_type_for_prompts_returns_422(self, client: TestClient) -> None:
        """``prompts`` must be a list; sending a string is a 422.

        Accepts both ``list_type`` and ``type_error`` Pydantic
        type-name prefixes so the test is stable across Pydantic
        minor versions where the suffix can drift.
        """
        with _StubbedDeps() as deps:
            r = client.post(
                "/v1/mods/generate/batch",
                json={"user_id": "u1", "prompts": "just one prompt"},
            )
        assert r.status_code == 422
        detail = r.json()["detail"]
        match = next(
            (e for e in detail
             if e.get("loc", [None, None])[-1] == "prompts"
             and ("list_type" in e.get("type", "")
                  or e.get("type", "").startswith("type_error"))),
            None,
        )
        assert match is not None, (
            f"no list-type error pinned to prompts; detail={detail!r}"
        )
        assert deps.create.await_count == 0
        assert deps.set_status.await_count == 0
        assert deps.bg.call_count == 0