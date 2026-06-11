"""Tests for startup configuration validation."""
import pytest


def test_validate_config_defaults_passes() -> None:
    """Default configuration values pass validation."""
    from app.config import validate_config

    validate_config()


def test_validate_config_rejects_high_t2_iterations(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_t2_iterations outside 0-2 fails validation."""
    import orchestrator.state
    from app.config import validate_config

    class FakeState:
        def __init__(self, **kwargs: object) -> None:
            self.max_t2_iterations = 3

    monkeypatch.setattr(orchestrator.state, "PipelineState", FakeState)

    with pytest.raises(RuntimeError, match="max_t2_iterations must be between 0 and 2"):
        validate_config()


def test_validate_config_rejects_excessive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """ZIP_OUTPUT_TIMEOUT >= 300 fails validation."""
    from app.config import get_config, validate_config

    cfg = get_config()
    monkeypatch.setattr(cfg, "zip_output_timeout", 300)

    with pytest.raises(RuntimeError, match="ZIP_OUTPUT_TIMEOUT must be > 0 and < 300"):
        validate_config()


def test_validate_config_rejects_zero_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """ZIP_OUTPUT_TIMEOUT <= 0 fails validation."""
    from app.config import get_config, validate_config

    cfg = get_config()
    monkeypatch.setattr(cfg, "zip_output_timeout", 0)

    with pytest.raises(RuntimeError, match="ZIP_OUTPUT_TIMEOUT must be > 0 and < 300"):
        validate_config()
