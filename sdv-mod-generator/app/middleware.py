"""Request-ID middleware — bind a stable request_id to every log line.

The ID is taken from the `X-Request-ID` request header if present (so
upstream load balancers / Discord can correlate), otherwise generated as
a short UUID. The ID is:
  - bound into structlog contextvars so every log line within the
    request includes `request_id`
  - echoed on the response as `X-Request-ID`
  - logged once at request completion with status + duration

This is the field operators search by when an end-user reports "my
request failed" — see docs/RUNBOOK.md.
"""
import time
import uuid
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_access_logger = structlog.get_logger("app.access")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"
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
