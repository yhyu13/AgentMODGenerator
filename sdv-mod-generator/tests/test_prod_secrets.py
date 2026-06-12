"""Tests for production secrets validation."""
import pytest


def _set_all_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all required production secrets."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "FAKEAWSACCESSKEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "FAKEAWSSECRETKEY")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-discord-token")
    monkeypatch.setenv("DISCORD_APP_ID", "123456789")
    monkeypatch.setenv("API_KEY", "fake-api-key")
    monkeypatch.setenv("API_OWNER_USER_ID", "owner123")


class TestRequireProdSecrets:
    """Tests for require_prod_secrets() function."""

    def test_returns_none_when_not_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """require_prod_secrets should return None when not in prod."""
        monkeypatch.setattr("app.config.IS_PROD", False)
        from app.config import require_prod_secrets
        assert require_prod_secrets() is None

    def test_passes_when_prod_and_all_secrets_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In prod with all secrets set, should return None."""
        monkeypatch.setattr("app.config.IS_PROD", True)
        _set_all_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
        from app.config import require_prod_secrets
        assert require_prod_secrets() is None

    def test_passes_when_prod_and_only_openai_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only OPENAI_API_KEY set should pass (at least one LLM key required)."""
        monkeypatch.setattr("app.config.IS_PROD", True)
        _set_all_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from app.config import require_prod_secrets
        assert require_prod_secrets() is None

    def test_passes_when_prod_and_only_anthropic_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only ANTHROPIC_API_KEY set should pass."""
        monkeypatch.setattr("app.config.IS_PROD", True)
        _set_all_secrets(monkeypatch)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
        from app.config import require_prod_secrets
        assert require_prod_secrets() is None

    def test_raises_when_prod_and_no_llm_key_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both LLM keys missing should raise RuntimeError."""
        monkeypatch.setattr("app.config.IS_PROD", True)
        _set_all_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        from app.config import require_prod_secrets
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY or ANTHROPIC_API_KEY"):
            require_prod_secrets()

    def test_raises_when_prod_and_missing_db_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing DATABASE_URL should raise RuntimeError."""
        monkeypatch.setattr("app.config.IS_PROD", True)
        _set_all_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        monkeypatch.setenv("DATABASE_URL", "")
        from app.config import require_prod_secrets
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            require_prod_secrets()
