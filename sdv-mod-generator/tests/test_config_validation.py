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


def test_validate_config_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid LOG_LEVEL fails validation."""
    from app.config import get_config, validate_config

    cfg = get_config()
    monkeypatch.setattr(cfg, "log_level", "TRACE")

    with pytest.raises(RuntimeError, match="LOG_LEVEL must be one of"):
        validate_config()


def test_validate_config_accepts_valid_log_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid LOG_LEVEL values pass validation."""
    from app.config import get_config, validate_config

    cfg = get_config()
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        monkeypatch.setattr(cfg, "log_level", level)
        validate_config()  # should not raise


def test_safe_int_parses_valid_integer() -> None:
    """_safe_int should parse valid integers."""
    from app.config import _safe_int

    assert _safe_int("120", 0) == 120
    assert _safe_int("0", 99) == 0
    assert _safe_int("-5", 99) == -5


def test_safe_int_falls_back_on_invalid_input() -> None:
    """_safe_int should fall back to default on invalid input."""
    from app.config import _safe_int

    assert _safe_int("abc", 120) == 120
    assert _safe_int("", 120) == 120
    assert _safe_int(None, 120) == 120
    assert _safe_int("12.5", 120) == 120


def test_required_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """_required should return the env var value when set."""
    from app.config import _required

    monkeypatch.setenv("TEST_VAR", "test_value")
    assert _required("TEST_VAR") == "test_value"


def test_required_returns_empty_in_dev_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_required should return empty string in dev when env var is missing."""
    from app.config import _required, IS_PROD

    monkeypatch.delenv("MISSING_VAR", raising=False)
    if not IS_PROD:
        assert _required("MISSING_VAR") == ""


def test_required_raises_in_prod_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_required should raise RuntimeError in prod when env var is missing."""
    import app.config as config_module
    from app.config import _required

    monkeypatch.setattr(config_module, "IS_PROD", True)
    monkeypatch.setattr(config_module, "APP_ENV", "prod")
    monkeypatch.delenv("MISSING_VAR", raising=False)

    with pytest.raises(RuntimeError, match="Required env var MISSING_VAR is missing"):
        _required("MISSING_VAR")
