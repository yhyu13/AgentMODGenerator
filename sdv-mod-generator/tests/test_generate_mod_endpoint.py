"""Tests for POST /v1/mods/generate (singular endpoint).

Mirrors ``tests/test_generate_mod_batch.py`` (the batch sibling) but
covers the singular handler ``generate_mod``. The batch test pins the
batch endpoint's 5-step dance (unique id, create_mod_request with
``phase='batch'``, redis_set_status with ``"running"``,
``run_pipeline_background``, ``BatchGenerateItem`` append) but does
NOT cover the singular path — without this file, a regression in
``generate_mod`` (e.g. accidentally switching the phase from
``"p1_shop_channel"`` to ``"batch"``, or wiring up
``run_pipeline_background`` AFTER ``redis_set_status`` instead of
BEFORE) would only surface as a silent failure in production.

Uses FastAPI TestClient (synchronous) so we don't need a running
uvicorn. Storage deps are stubbed via AsyncMock + monkeypatch —
same recipe as ``tests/test_generate_mod_batch.py`` and
``tests/test_health_metrics.py``. The handler re-reads the request
body via ``await request.json()`` (line 137 of
``app/api/routes.py``); TestClient's Request encoding handles that
transparently so no fake Request fixture is needed.

Distinct from the batch test in two ways:
1. ``create_mod_request`` is called with ``phase="p1_shop_channel"``
   (the legacy hardcoded default for the singular endpoint — the
   BATCH endpoint uses ``"batch"``; this asymmetry is intentional
   and a str-dedupe refactor would surface as a phase mismatch).
2. ``run_pipeline_background`` is called exactly ONCE (the batch
   version calls it N times for N prompts).
"""
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


REQ_ID_RE = re.compile(r"^req_[0-9a-f]{12}$")


class _StubbedDeps:
    """Helper bundling the 3 stubbed deps + a context-manager apply.

    Every test uses the same triple (AsyncMock + AsyncMock + MagicMock)
    wired into the same 3 monkeypatch targets. Pulling them into a
    single ``with`` block + attribute access keeps each test method
    to a single ``mp.setattr`` line per dep.
    """

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


class TestGenerateModEndpointIdContract:
    """Pin: request_id shape is ``req_<12 hex>`` and status='running'."""

    def test_request_id_matches_req_12hex_format(self, client: TestClient) -> None:
        with _StubbedDeps() as deps:
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u1", "prompt": "make a TV shopping channel"},
            )
        assert r.status_code == 200
        assert REQ_ID_RE.match(r.json()["request_id"]), (
            f"request_id {r.json()['request_id']!r} does not match {REQ_ID_RE.pattern}"
        )

    def test_status_is_running_in_response(self, client: TestClient) -> None:
        """Non-blocking contract: returns immediately with status='running'."""
        with _StubbedDeps():
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u1", "prompt": "test prompt"},
            )
        assert r.status_code == 200
        assert r.json()["status"] == "running"


class TestGenerateModEndpointStorageCalls:
    """Pin: each downstream callable fires exactly once with the right args."""

    def test_create_mod_request_phase_p1_shop_channel(self, client: TestClient) -> None:
        with _StubbedDeps() as deps:
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "user-7", "prompt": "make a shopping channel"},
            )
        assert r.status_code == 200
        assert deps.create.await_count == 1
        args = deps.create.await_args_list[0].args
        assert len(args) == 6
        assert args[1] == "user-7", "user_id not threaded through"
        assert args[3] == "p1_shop_channel", (
            f"expected phase='p1_shop_channel' (singular endpoint's legacy "
            f"default — BATCH endpoint uses 'batch'); got phase={args[3]!r}"
        )
        assert args[4] == [] and args[5] == {}

    def test_run_pipeline_background_called_once(self, client: TestClient) -> None:
        with _StubbedDeps() as deps:
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u3", "prompt": "design a fishing overhaul"},
            )
        assert r.status_code == 200
        assert deps.bg.call_count == 1
        args = deps.bg.call_args_list[0].args
        assert len(args) == 3
        assert args[1] == "u3"
        assert args[2] == "design a fishing overhaul"

    def test_set_status_runs_before_pipeline(self, client: TestClient) -> None:
        """``redis_set_status('running')`` is awaited BEFORE
        ``run_pipeline_background`` is called.

        Avoids the race where the pipeline's first mutation races the
        cache-key creation. Mirrors the batch endpoint's contract.
        """
        order: list[str] = []

        async def _track_create(*args, **kwargs):
            order.append("create")
        async def _track_set_status(*args, **kwargs):
            order.append("set_status")
        def _track_bg(*args, **kwargs):
            order.append("bg")

        with _StubbedDeps() as deps:
            deps.create.side_effect = _track_create
            deps.set_status.side_effect = _track_set_status
            deps.bg.side_effect = _track_bg
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u", "prompt": "make a texture mod"},
            )
        assert r.status_code == 200
        assert deps.set_status.await_args_list[0].args[1] == "running"
        assert order == ["create", "set_status", "bg"], (
            f"call order must be create → set_status → bg, got {order!r}"
        )


class TestGenerateModEndpointEstimateSeconds:
    """Pin: ``estimated_seconds`` mirrors ``_estimate_seconds(prompt)``.

    Same 4-keyword-group parametrization as the batch test (texture=30,
    npc=60, farm=75, default=90). A refactor that drifts the singular
    endpoint's estimate from the batch endpoint's would surface here.
    """

    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("replace a parsnip sprite", 30),     # texture
            ("add NPC dialogue for Leah", 60),    # npc
            ("farm expansion building warp", 75), # farm-expansion
            ("make a TV shopping channel", 90),   # default fallthrough
        ],
    )
    def test_estimated_seconds_reflects_prompt_group(
        self, client: TestClient, prompt: str, expected: int,
    ) -> None:
        with _StubbedDeps():
            r = client.post(
                "/v1/mods/generate",
                json={"user_id": "u", "prompt": prompt},
            )
        assert r.status_code == 200
        assert r.json()["estimated_seconds"] == expected