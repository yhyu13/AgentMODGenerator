"""Tests for API schema validation."""

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    ModStatusResponse,
    FilePreviewResponse,
    HistoryEntry,
    HistoryResponse,
    ErrorResponse,
)


class TestGenerateRequest:
    def test_valid_request(self):
        req = GenerateRequest(user_id="user_123", prompt="make a shop mod")
        assert req.user_id == "user_123"
        assert req.prompt == "make a shop mod"

    def test_prompt_too_long(self):
        with pytest.raises(ValidationError) as exc_info:
            GenerateRequest(user_id="user_123", prompt="x" * 10001)
        assert "prompt" in str(exc_info.value)

    def test_empty_user_id(self):
        req = GenerateRequest(user_id="", prompt="test")
        assert req.user_id == ""

    def test_optional_phase(self):
        req = GenerateRequest(user_id="user_123", prompt="test", phase="shop_channel")
        assert req.phase == "shop_channel"

    def test_phase_none_by_default(self):
        req = GenerateRequest(user_id="user_123", prompt="test")
        assert req.phase is None


class TestGenerateResponse:
    def test_valid_response(self):
        resp = GenerateResponse(request_id="req_123", status="running", estimated_seconds=90)
        assert resp.request_id == "req_123"
        assert resp.status == "running"
        assert resp.estimated_seconds == 90

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            GenerateResponse(request_id="req_123", status="invalid_status")

    def test_optional_estimated_seconds(self):
        resp = GenerateResponse(request_id="req_123", status="done")
        assert resp.estimated_seconds is None


class TestModStatusResponse:
    def test_valid_status(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        resp = ModStatusResponse(
            request_id="req_123",
            status="done",
            created_at=now,
        )
        assert resp.status == "done"
        assert resp.progress_percent is None

    def test_progress_percent_bounds(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            ModStatusResponse(
                request_id="req_123",
                status="running",
                progress_percent=101,
                created_at=now,
            )

    def test_progress_percent_negative(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            ModStatusResponse(
                request_id="req_123",
                status="running",
                progress_percent=-1,
                created_at=now,
            )

    def test_optional_fields(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        resp = ModStatusResponse(
            request_id="req_123",
            status="running",
            created_at=now,
        )
        assert resp.zip_url is None
        assert resp.t2_score is None
        assert resp.t2_feedback is None


class TestFilePreviewResponse:
    def test_valid_response(self):
        resp = FilePreviewResponse(request_id="req_123", files={"manifest.json": {}})
        assert resp.request_id == "req_123"
        assert "manifest.json" in resp.files


class TestHistoryEntry:
    def test_valid_entry(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        entry = HistoryEntry(
            request_id="req_123",
            prompt="make a shop",
            status="done",
            created_at=now,
        )
        assert entry.status == "done"


class TestHistoryResponse:
    def test_valid_response(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        entry = HistoryEntry(
            request_id="req_123",
            prompt="make a shop",
            status="done",
            created_at=now,
        )
        resp = HistoryResponse(user_id="user_123", entries=[entry])
        assert len(resp.entries) == 1
        assert resp.entries[0].request_id == "req_123"


class TestErrorResponse:
    def test_valid_response(self):
        resp = ErrorResponse(detail="Not found", code="NOT_FOUND")
        assert resp.detail == "Not found"
        assert resp.code == "NOT_FOUND"
