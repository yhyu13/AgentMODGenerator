"""Tests for P5.3 — /health/deep and /metrics endpoints.

Uses FastAPI TestClient (synchronous) so we don't need a running uvicorn.
External services (DB, Redis, S3) are stubbed — we are testing the
plumbing, not the dependency clients.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Importing the app triggers lifespan wiring. We don't want real
# Postgres / Redis connections during these tests, so we patch the
# dependency probes before any request runs.
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_liveness(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "discord_bot_ready" in body
    assert "ts" in body


def test_health_deep_all_ok(client: TestClient) -> None:
    """When every probe passes, /health/deep returns 200."""
    with (
        patch("app.health._ping_db", new=AsyncMock(return_value=None)),
        patch("app.health._ping_redis", new=AsyncMock(return_value=None)),
        patch("app.health._ping_s3", new=AsyncMock(return_value=None)),
        patch("app.health._ping_bot", new=AsyncMock(return_value={
            "name": "discord_bot", "ok": True, "latency_ms": 42,
        })),
    ):
        r = client.get("/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    names = {c["name"] for c in body["checks"]}
    assert names == {"postgres", "redis", "s3", "discord_bot"}


def test_health_deep_db_down(client: TestClient) -> None:
    """If DB is down, status is 503 and the body pinpoints which check failed."""
    async def boom() -> None:
        raise ConnectionError("db unreachable")
    with (
        patch("app.health._ping_db", new=AsyncMock(side_effect=boom)),
        patch("app.health._ping_redis", new=AsyncMock(return_value=None)),
        patch("app.health._ping_s3", new=AsyncMock(return_value=None)),
        patch("app.health._ping_bot", new=AsyncMock(return_value={
            "name": "discord_bot", "ok": True, "latency_ms": 42,
        })),
    ):
        r = client.get("/health/deep")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    db = next(c for c in body["checks"] if c["name"] == "postgres")
    assert db["ok"] is False
    assert "db unreachable" in db["error"]


def test_health_deep_bot_not_started(client: TestClient) -> None:
    """If the bot hasn't reached on_ready, /health/deep says so."""
    with (
        patch("app.health._ping_db", new=AsyncMock(return_value=None)),
        patch("app.health._ping_redis", new=AsyncMock(return_value=None)),
        patch("app.health._ping_s3", new=AsyncMock(return_value=None)),
        patch("app.health._ping_bot", new=AsyncMock(return_value={
            "name": "discord_bot", "ok": False, "error": "bot_not_started",
        })),
    ):
        r = client.get("/health/deep")
    assert r.status_code == 503
    bot = next(c for c in r.json()["checks"] if c["name"] == "discord_bot")
    assert bot["ok"] is False


def test_metrics_endpoint_returns_prometheus_format(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Our metric names must be present in the output so Prometheus can
    # scrape them. We do not assert specific values (counters are
    # process-wide and may have non-zero state from earlier tests).
    assert "sdv_api_requests_total" in body
    assert "sdv_api_request_duration_seconds" in body
    assert "sdv_pipeline_runs_total" in body
    assert "sdv_pipeline_t2_score" in body
    assert "sdv_pipeline_generators_failed_total" in body
    assert "sdv_pipeline_generators_succeeded_total" in body
    assert "sdv_dependency_up" in body


def test_request_counter_increments(client: TestClient) -> None:
    """Hitting /health increments the request counter."""
    from app.metrics import API_REQUESTS_TOTAL

    def _count() -> float:
        # All label combos for /health; sum them.
        total = 0.0
        for metric in API_REQUESTS_TOTAL.collect():
            for sample in metric.samples:
                if sample.name.endswith("_total"):
                    total += sample.value
        return total

    before = _count()
    client.get("/health")
    after = _count()
    assert after > before
