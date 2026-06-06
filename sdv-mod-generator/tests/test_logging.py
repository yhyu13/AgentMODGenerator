"""Tests for P5.4 — structured logging contract.

Covers:
  - JSON output shape in prod (default)
  - Console output in dev (LOG_FORMAT=console)
  - LOG_LEVEL honored
  - request_id is bound to context and included in log lines
  - Logs flow through the same structlog pipeline as stdlib logging
"""
import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from app.middleware import REQUEST_ID_HEADER
from app import logging_config
from app.main import app


class _CapturingHandler(logging.Handler):
    """A logging.Handler that records the formatted message of every record.

    We attach this to the root logger for the duration of a test, so we
    can assert on the JSON text that would have been written to stdout.
    Using a handler is more reliable than `capsys` / `capfd` because those
    don't reliably capture the stream object that StreamHandler holds.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


@pytest.fixture
def captured_logs() -> _CapturingHandler:
    """Attach a capturing handler that mirrors the production formatter.

    The formatter uses JSONRenderer (the prod default). Tests that want
    to exercise LOG_FORMAT=console should set the format on the handler
    themselves; LOG_FORMAT=console re-runs `_configure()` which only
    touches the root handlers, not the one we attached here.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    foreign_pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    handler = _CapturingHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _parse_lines(handler: _CapturingHandler) -> list[dict]:
    return _parse_lines_from(handler.records)


def _parse_lines_from(records: list[str]) -> list[dict]:
    out: list[dict] = []
    for line in records:
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def test_json_log_format_includes_required_fields(
    captured_logs: _CapturingHandler,
) -> None:
    """A structlog logger call produces a JSON line with the contract fields."""
    logger = logging_config.get_logger("test.module")
    logger.info("test.event.fired", foo="bar", n=42)
    records = _parse_lines(captured_logs)
    assert any(r.get("event") == "test.event.fired" for r in records), records
    rec = next(r for r in records if r["event"] == "test.event.fired")
    assert rec["level"] == "info"
    assert rec["foo"] == "bar"
    assert rec["n"] == 42
    assert "timestamp" in rec
    assert rec["logger"] == "test.module"


def test_request_id_header_is_echoed() -> None:
    """An X-Request-ID from upstream is preserved on the response."""
    client = TestClient(app)
    r = client.get("/health", headers={REQUEST_ID_HEADER: "req_abc123"})
    assert r.status_code == 200
    assert r.headers[REQUEST_ID_HEADER] == "req_abc123"


def test_request_id_is_generated_when_missing() -> None:
    """Without an X-Request-ID, the middleware mints one (req_ prefix)."""
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    rid = r.headers[REQUEST_ID_HEADER]
    assert rid.startswith("req_")
    assert len(rid) >= len("req_") + 8


def test_request_id_binds_to_structlog_context(
    captured_logs: _CapturingHandler,
) -> None:
    """The http.request.done log line includes the request_id."""
    client = TestClient(app)
    r = client.get("/health", headers={REQUEST_ID_HEADER: "req_integration_test"})
    assert r.status_code == 200
    records = _parse_lines(captured_logs)
    matching = [r for r in records if r.get("event") == "http.request.done"]
    assert matching, "expected http.request.done log line"
    rec = matching[-1]
    assert rec["request_id"] == "req_integration_test"
    assert rec["method"] == "GET"
    assert "duration_ms" in rec


def test_log_level_filters_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOG_LEVEL=WARNING suppresses INFO.

    We test the effect on the *real* root logger handler — the one
    installed by `logging_config._configure()` — rather than our
    capturing handler (which has its own level inherited from the
    fixture). The capturing handler's level is set to NOTSET so it
    always records; the filter happens at the root logger level.
    """
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logging_config._configure()
    records: list[str] = []
    h = _CapturingHandler()
    h.setLevel(logging.DEBUG)
    h.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    logging.getLogger().addHandler(h)
    try:
        logger = logging_config.get_logger("test.level")
        logger.info("should.be.filtered")
        logger.warning("should.appear")
        records = list(h.records)
    finally:
        logging.getLogger().removeHandler(h)
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        logging_config._configure()

    parsed = _parse_lines_from(records)
    events = {r.get("event") for r in parsed}
    assert "should.be.filtered" not in events, parsed
    assert "should.appear" in events, parsed


def test_console_format_is_human_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOG_FORMAT=console switches the real stdout handler to ConsoleRenderer."""
    monkeypatch.setenv("LOG_FORMAT", "console")
    logging_config._configure()
    # Find the real stdout handler (the one configure() just installed).
    handlers = logging.getLogger().handlers
    stdout_handler = next(h for h in handlers if hasattr(h, "stream"))
    fmt = stdout_handler.formatter
    # The ProcessorFormatter wraps a list of processors; the last one is
    # the renderer. For console mode it's a ConsoleRenderer.
    from structlog.dev import ConsoleRenderer

    assert any(isinstance(p, ConsoleRenderer) for p in fmt.processors)
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_config._configure()


def test_stdlib_logging_routes_through_structlog(
    captured_logs: _CapturingHandler,
) -> None:
    """Plain `logging.getLogger().info(...)` produces a JSON line too.

    This is what we rely on for uvicorn, sqlalchemy, discord.py, etc.
    The line must include the same envelope (timestamp, level, event,
    logger) as a structlog-originated call so the log shipper sees one
    uniform format.
    """
    stdlib_logger = logging.getLogger("third.party.lib")
    stdlib_logger.info("from.stdlib")
    records = _parse_lines(captured_logs)
    matching = [r for r in records if r.get("event") == "from.stdlib"]
    assert matching, records
    rec = matching[-1]
    assert rec["level"] == "info"
    assert rec["logger"] == "third.party.lib"
    assert "timestamp" in rec
