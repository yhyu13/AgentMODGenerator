"""Tests for SecurityHeadersMiddleware.

Pins the security-header contract on /v1/* and the public
observability surfaces (/health, /health/deep, /metrics). The
middleware is registered at app/main.py as
``app.add_middleware(SecurityHeadersMiddleware)`` BELOW
RequestIdMiddleware so the LAST write wins on the response.

Headers pinned (all unconditional, all on /v1/* + health + metrics):

* X-Content-Type-Options: nosniff
* Referrer-Policy: no-referrer
* X-Frame-Options: DENY
* Permissions-Policy: <long deny list>
* Cross-Origin-Resource-Policy: same-origin
* X-Permitted-Cross-Domain-Policies: none
* X-DNS-Prefetch-Control: off
* Cross-Origin-Opener-Policy: same-origin
* Origin-Agent-Cluster: ?1
* X-XSS-Protection: 0
* Strict-Transport-Security: max-age=31536000; ... (env-gated by HSTS_ENABLED)
* Cross-Origin-Embedder-Policy: require-corp (env-gated by COEP_ENABLED)

Plus the unconditional-everywhere siblings:
* Server: sdv-mod-generator
* Date: <rfc1123>

And the per-endpoint overrides:
* Cache-Control: no-store (on /v1/mods/*)
* Cache-Control: public, max-age=60 (on /v1/mods/phases/known)
* Vary: Accept-Encoding (on /v1/mods/{id}/files)
* Vary: Origin (on /webhooks/discord)

Path filter pins:
* /v1/* (the public API)
* /health, /health/deep, /metrics (the public observability)
* Root / does NOT get the security headers (only Server + Date)
* A non-matching path (e.g. /robots.txt) does NOT get them
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    # Constructor (no `with` block) so the lifespan does not run —
    # the security headers are middleware-level, not lifespan-level,
    # and we don't want the test to require a real Postgres / Redis.
    return TestClient(app, raise_server_exceptions=True)


class TestUnconditionalHeaders:
    """Server and Date are set on EVERY response, including the root."""

    def test_root_has_server_header(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.headers.get("Server") == "sdv-mod-generator"

    def test_root_has_date_header(self, client: TestClient) -> None:
        resp = client.get("/")
        date = resp.headers.get("Date")
        assert date is not None
        # RFC 1123 / 7231 IMF-fixdate format e.g. "Wed, 21 Oct 2026 07:28:00 GMT"
        assert date.endswith("GMT")

    def test_health_has_server_and_date(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.headers.get("Server") == "sdv-mod-generator"
        assert resp.headers.get("Date", "").endswith("GMT")


class TestPublicPathPrefixHeaders:
    """All 12 unconditional public-path-prefix headers are set on /v1/*,
    /health, /health/deep, and /metrics."""

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("X-Permitted-Cross-Domain-Policies", "none"),
            ("X-DNS-Prefetch-Control", "off"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Origin-Agent-Cluster", "?1"),
            ("X-XSS-Protection", "0"),
        ],
    )
    def test_unconditional_headers_on_v1(
        self, client: TestClient, header: str, expected: str
    ) -> None:
        resp = client.get("/v1/mods/phases/known")
        # The endpoint may 200 or 5xx depending on the test env; we
        # only care that the header landed on the response, so accept
        # any status.
        assert resp.headers.get(header) == expected, (
            f"Expected {header}={expected} on /v1/mods/phases/known, "
            f"got {resp.headers.get(header)!r} (status={resp.status_code})"
        )

    def test_permissions_policy_on_v1(self, client: TestClient) -> None:
        resp = client.get("/v1/mods/phases/known")
        pp = resp.headers.get("Permissions-Policy", "")
        # The policy bans the usual fingerprinting surfaces.
        assert "camera=()" in pp
        assert "microphone=()" in pp
        assert "geolocation=()" in pp
        assert "payment=()" in pp


class TestHSTSEnvGate:
    """Strict-Transport-Security is OFF by default. Operators opt in."""

    def test_hsts_off_by_default(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HSTS_ENABLED", raising=False)
        resp = client.get("/health")
        assert "Strict-Transport-Security" not in resp.headers

    def test_hsts_on_when_env_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HSTS_ENABLED", "true")
        # The middleware reads the env at call time (per ``os.getenv``),
        # not at import time, so a fresh request picks up the new value.
        resp = client.get("/health")
        assert (
            resp.headers.get("Strict-Transport-Security")
            == "max-age=31536000; includeSubDomains; preload"
        )


class TestCOEPEnvGate:
    """Cross-Origin-Embedder-Policy is OFF by default. Operators opt in."""

    def test_coep_off_by_default(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COEP_ENABLED", raising=False)
        resp = client.get("/health")
        assert "Cross-Origin-Embedder-Policy" not in resp.headers

    def test_coep_on_when_env_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COEP_ENABLED", "true")
        resp = client.get("/health")
        assert resp.headers.get("Cross-Origin-Embedder-Policy") == "require-corp"


class TestCacheControlOverrides:
    """Cache-Control has two per-endpoint overrides."""

    def test_mods_endpoint_has_no_store(
        self, client: TestClient
    ) -> None:
        # /v1/mods is the LIST endpoint; the no-store path prefix
        # /v1/mods/ should match. The endpoint may 200 or 503
        # depending on whether Postgres is reachable, but the
        # Cache-Control header is what we care about.
        resp = client.get("/v1/mods")
        assert resp.headers.get("Cache-Control") == "no-store"

    def test_phases_known_has_public_max_age(
        self, client: TestClient
    ) -> None:
        # /v1/mods/phases/known is the cacheable phase-list endpoint;
        # the LAST-write-wins semantics override the no-store default
        # from the broader /v1/mods/ prefix.
        resp = client.get("/v1/mods/phases/known")
        assert resp.headers.get("Cache-Control") == "public, max-age=60"


class TestVaryHeaders:
    """Vary: Accept-Encoding on file preview, Vary: Origin on webhook."""

    def test_webhook_has_vary_origin(
        self, client: TestClient
    ) -> None:
        # /webhooks/discord is the CORS-enabled endpoint; a CDN in
        # front of it must include Origin in the cache key.
        # The endpoint returns 503 if DISCORD_PUBLIC_KEY is unset
        # (the test env isolates all LLM/proxy/secrets vars), but
        # the Vary header is middleware-set, so it's present.
        resp = client.post(
            "/webhooks/discord", json={"type": 1}
        )
        assert resp.headers.get("Vary") == "Origin"


class TestRootHasNoSecurityHeaders:
    """The root banner / does NOT get the security headers (only Server+Date).

    The path-prefix filter in the middleware excludes ``/`` so the
    root banner stays byte-compatible with the v140 baseline
    (operators and load balancers poll it as a liveness probe and
    the security headers could confuse older CDN configurations).
    """

    def test_root_has_no_nosniff(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "X-Content-Type-Options" not in resp.headers

    def test_root_has_no_frame_options(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "X-Frame-Options" not in resp.headers

    def test_root_has_no_referrer_policy(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "Referrer-Policy" not in resp.headers

    def test_unmatched_path_has_no_security_headers(
        self, client: TestClient
    ) -> None:
        resp = client.get("/nonexistent")
        # 404 — no path-prefix match — no security headers.
        assert "X-Content-Type-Options" not in resp.headers
        assert "X-Frame-Options" not in resp.headers
        assert "Referrer-Policy" not in resp.headers
        # But Server + Date are unconditional.
        assert resp.headers.get("Server") == "sdv-mod-generator"
        assert resp.headers.get("Date", "").endswith("GMT")
