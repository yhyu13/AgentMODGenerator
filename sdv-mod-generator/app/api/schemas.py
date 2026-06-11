"""API request/response schemas."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    user_id: str
    prompt: str = Field(max_length=10000)
    phase: str | None = None


class GenerateResponse(BaseModel):
    request_id: str
    status: Literal["pending", "running", "done", "failed"]
    estimated_seconds: int | None = Field(default=None, description="Estimated time to completion in seconds")


class ModStatusResponse(BaseModel):
    request_id: str
    status: Literal["pending", "running", "done", "failed"]
    zip_url: str | None = None
    files_preview: list[str] = Field(default_factory=list)
    t1_errors: list[str] = Field(default_factory=list)
    generators_failed: list[str] = Field(default_factory=list)
    generators_succeeded: list[str] = Field(default_factory=list)
    t2_feedback: str | None = None
    t2_score: int | None = None
    t2_max_score: int | None = None
    t2_pass_threshold: int | None = None
    t2_passed: bool | None = None
    t2_available: bool | None = None
    t2_panel_passed_count: int | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100, description="Pipeline progress percentage")
    current_stage: str | None = Field(default=None, description="Current pipeline stage")
    created_at: datetime


class FilePreviewResponse(BaseModel):
    request_id: str
    files: dict[str, Any]


class HistoryEntry(BaseModel):
    request_id: str
    prompt: str
    status: str
    created_at: datetime


class HistoryResponse(BaseModel):
    user_id: str
    entries: list[HistoryEntry]


class ErrorResponse(BaseModel):
    detail: str
    code: str