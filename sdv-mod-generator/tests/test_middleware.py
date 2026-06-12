"""Tests for RequestIdMiddleware."""

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from app.middleware import RequestIdMiddleware, REQUEST_ID_HEADER


def _make_app(routes):
    app = Starlette(routes=routes)
    app.add_middleware(RequestIdMiddleware)
    return app


class TestRequestIdMiddleware:
    def test_generates_request_id_when_header_missing(self):
        async def test_endpoint(request: Request):
            return Response("ok")

        app = _make_app([Route("/test", test_endpoint)])
        client = TestClient(app)
        response = client.get("/test")
        assert REQUEST_ID_HEADER in response.headers
        assert response.headers[REQUEST_ID_HEADER].startswith("req_")

    def test_uses_existing_request_id_header(self):
        async def test_endpoint(request: Request):
            return Response("ok")

        app = _make_app([Route("/test", test_endpoint)])
        client = TestClient(app)
        existing_id = "existing-request-id-123"
        response = client.get("/test", headers={REQUEST_ID_HEADER: existing_id})
        assert response.headers[REQUEST_ID_HEADER] == existing_id

    def test_logs_on_success(self, caplog):
        async def test_endpoint(request: Request):
            return Response("ok")

        app = _make_app([Route("/test", test_endpoint)])
        client = TestClient(app)
        with caplog.at_level("INFO"):
            response = client.get("/test")
        assert response.status_code == 200
        assert "http.request.done" in caplog.text

    def test_logs_on_error(self, caplog):
        async def error_endpoint(request: Request):
            raise ValueError("test error")

        app = _make_app([Route("/error", error_endpoint)])
        client = TestClient(app)
        with caplog.at_level("ERROR"):
            with pytest.raises(ValueError, match="test error"):
                client.get("/error")
        assert "http.request.error" in caplog.text
