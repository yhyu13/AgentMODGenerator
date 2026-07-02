"""HTTP middleware: request ID + security headers.

Two middlewares live here:

* ``RequestIdMiddleware`` — bind a stable ``request_id`` to every log
  line. The ID is taken from the inbound ``X-Request-ID`` header if
  present (so upstream load balancers / Discord can correlate),
  otherwise generated as a short UUID. It is bound into structlog
  contextvars, echoed on the response as ``X-Request-ID``, and logged
  once at request completion with status + duration.

* ``SecurityHeadersMiddleware`` — emit OWASP-recommended security
  response headers (``X-Content-Type-Options``, ``Referrer-Policy``,
  ``X-Frame-Options``, ``Permissions-Policy``, ``Cross-Origin-*``,
  ``Strict-Transport-Security`` (env-gated), ``X-XSS-Protection``,
  etc.) on every endpoint under ``/v1/*`` and the public
  observability surfaces ``/health``, ``/health/deep``, ``/metrics``.
  The class is opt-in via ``app.add_middleware``; it does NOT touch
  ``/`` (the root banner) or unmatched paths.

The path-prefix sets are constants at module level so a future PR
that needs to add a sibling header has one obvious place to extend.
"""
import os
import time
import uuid
from email.utils import formatdate
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# ---------------------------------------------------------------------------
# Path-prefix sets
# ---------------------------------------------------------------------------
# Every SecurityHeadersMiddleware sibling header (nosniff, referrer-policy,
# frame-options, permissions-policy, CORP, X-Permitted-Cross-Domain, HSTS,
# X-DNS-Prefetch-Control, COOP, COEP, OAC, X-XSS-Protection) uses the same
# 4-tuple of paths so all 13 siblings stay in lockstep. Adding a new path?
# Add it to every tuple. Renaming a sibling? Touch every tuple.

_PUBLIC_PATH_PREFIXES = ("/v1/", "/health", "/health/deep", "/metrics")
_NO_STORE_PATH_PREFIXES = ("/v1/mods/",)
_CACHEABLE_PATH_PREFIXES = ("/v1/mods/phases/known",)
_VARY_ORIGIN_PATH_PREFIXES = ("/webhooks/discord",)

_access_logger = structlog.get_logger("app.access")


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    """True if ``path`` is exactly one of the prefixes or starts with
    one of them (with or without a trailing slash). The form
    ``path == p[:-1]`` lets ``/v1/mods/`` (the prefix) match
    ``/v1/mods`` (the actual route registered without a trailing
    slash), and the unstrict ``path.startswith(p)`` form lets
    ``/v1/`` match ``/v1/mods``. The cost is a hypothetical
    ``/v10/foo`` route would also match ``/v1/``, but no such
    route exists today and the trade-off is acceptable."""
    return any(
        path == p
        or path == p.rstrip("/")
        or path.startswith(p)
        for p in prefixes
    )


# ---------------------------------------------------------------------------
# RequestIdMiddleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a stable ``request_id`` to every log line on the request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (
            request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"
        )
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _access_logger.exception(
                "http.request.error",
                status=500,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id
        _access_logger.info(
            "http.request.done",
            status=response.status_code,
            duration_ms=duration_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Emit OWASP-recommended security response headers.

    Each header is gated by a path-prefix set so the headers only land
    on the surfaces that benefit (the public API under ``/v1/*`` and
    the health/metrics endpoints). The class is registered BELOW the
    CORS middleware in ``app/main.py`` so it wraps outermost and sets
    the header last on every response (including ones CORSMiddleware
    may short-circuit).

    Two headers are env-gated:

    * ``Strict-Transport-Security`` (HSTS) — gated by ``HSTS_ENABLED``
      (default ``false``). Operators must audit the TLS path before
      enabling. Once enabled, browsers refuse to talk to the API
      over plain HTTP for ``max-age`` seconds (currently 1 year).
    * ``Cross-Origin-Embedder-Policy`` (COEP) — gated by
      ``COEP_ENABLED`` (default ``false``). Operators must audit the
      subresource inventory before enabling; a future static-asset
      route that loads a third-party font without ``CORP`` would 404.

    All other headers are unconditional defaults — they cost nothing
    and align with the OWASP "Secure Headers Project" recommendations.

    Header order in this class matches the v140-v166 sibling chain on
    discord-ops-hardening; the last-write-wins semantics on Starlette
    ``MutableHeaders`` mean the order is informational only.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        # ``scope["route"]`` is populated by Starlette when a route
        # matched; for 404s fall back to ``request.url.path`` so the
        # path-prefix filter still runs and a future ``/v1/<typo>``
        # 404 also gets the header (defence in depth).
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path

        if _matches(path, _PUBLIC_PATH_PREFIXES):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Permissions-Policy"] = (
                "accelerometer=(), browsing-topics=(), camera=(), "
                "geolocation=(), gyroscope=(), magnetometer=(), "
                "microphone=(), payment=(), usb=()"
            )
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
            response.headers["X-DNS-Prefetch-Control"] = "off"
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            response.headers["Origin-Agent-Cluster"] = "?1"
            response.headers["X-XSS-Protection"] = "0"

            if os.getenv("HSTS_ENABLED", "false").lower() == "true":
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains; preload"
                )
            if os.getenv("COEP_ENABLED", "false").lower() == "true":
                response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"

        if _matches(path, _NO_STORE_PATH_PREFIXES):
            response.headers["Cache-Control"] = "no-store"

        if _matches(path, _CACHEABLE_PATH_PREFIXES):
            # LAST write wins: the no-store default above is correctly
            # bypassed for this specific endpoint.
            response.headers["Cache-Control"] = "public, max-age=60"

        # File-preview content-negotiation cache-key correctness.
        # A CDN in front of the API must include the Accept header
        # in the cache key so JSON vs HTML variants of /files don't
        # poison each other.
        if request.url.path.startswith("/v1/mods/") and request.url.path.endswith(
            "/files"
        ):
            existing_vary = response.headers.get("vary")
            if not existing_vary or not existing_vary.strip():
                response.headers["Vary"] = "Accept-Encoding"
            else:
                # RFC 7230 §3.2.2: Vary is a comma-separated list. Append
                # Accept-Encoding if not already present (case-insensitive).
                # Strict CDNs (CloudFront, Cloudflare, Fastly) reject
                # duplicate Vary headers with 502, so combine into a
                # single value.
                fields = [v.strip() for v in existing_vary.split(",")]
                if not any(v.lower() == "accept-encoding" for v in fields):
                    fields.append("Accept-Encoding")
                response.headers["Vary"] = ", ".join(fields)

        if _matches(path, _VARY_ORIGIN_PATH_PREFIXES):
            # CORS cache-poisoning defence: a CDN must include the
            # Origin header in the cache key for the CORS-enabled
            # webhook endpoint, otherwise a preflight response for
            # one origin could be served to a different origin.
            response.headers["Vary"] = "Origin"

        # Unconditional headers (no path-prefix filter). Apply to
        # every response, including the root banner and 404s.
        # ``Server`` reduces the fingerprinting surface vs uvicorn's
        # default ``Server: uvicorn``. ``Date`` closes the RFC 7231
        # §7.1.1.1 recommendation that Starlette does NOT set.
        response.headers["Server"] = "sdv-mod-generator"
        response.headers["Date"] = formatdate(time.time(), usegmt=True)

        return response
