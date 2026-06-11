"""Environment configuration — loads from .env in dev, OS env in prod.

Set APP_ENV=prod to skip .env auto-loading and require secrets via the OS
environment (or mounted secrets from a secrets manager). The companion
`config/prod.env.example` documents which variables must be set.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_ENV = os.getenv("APP_ENV", "dev").lower()
IS_PROD = APP_ENV in ("prod", "production")

# Only auto-load the dev .env when not in production. In prod, all secrets
# must come from the OS environment (or a mounted secrets file that the
# orchestrator injects as env vars at startup).
if not IS_PROD:
    _dotenv_path = Path(__file__).parent.parent / "config" / ".env"
    if _dotenv_path.exists():
        load_dotenv(_dotenv_path, override=True)


# Required in production. At least one LLM provider key must also be present.
_REQUIRED_PROD_SECRETS: list[str] = [
    "DATABASE_URL",
    "REDIS_URL",
    "S3_BUCKET",
    "S3_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_APP_ID",
    "API_KEY",
    "API_OWNER_USER_ID",
]


def _required(name: str) -> str:
    """Read a required env var; raise with a clear message in prod if missing."""
    value = os.getenv(name, "").strip()
    if not value:
        if IS_PROD:
            raise RuntimeError(
                f"Required env var {name} is missing in APP_ENV={APP_ENV}. "
                f"See config/prod.env.example for the full list."
            )
        return ""
    return value


@dataclass
class Config:
    app_env: str = APP_ENV
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "MiniMax-M2.7")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_base_url: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost:5432/sdv_mods")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    s3_bucket: str = os.getenv("S3_BUCKET", "sdv-mod-generator")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")
    discord_app_id: str = os.getenv("DISCORD_APP_ID", "")
    api_key: str = os.getenv("API_KEY", "")
    api_owner_user_id: str = os.getenv("API_OWNER_USER_ID", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    zip_output_timeout: int = int(os.getenv("ZIP_OUTPUT_TIMEOUT", "120"))


_config_instance: Config | None = None


def get_config() -> Config:
    """Return the singleton Config instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def require_prod_secrets() -> None:
    """Validate that every required production secret is present.

    Raises RuntimeError on the first missing secret so operators get a clear
    startup failure instead of a cryptic downstream error.
    """
    if not IS_PROD:
        return

    missing: list[str] = []
    for name in _REQUIRED_PROD_SECRETS:
        value = os.getenv(name, "").strip()
        if not value:
            missing.append(name)

    if not os.getenv("OPENAI_API_KEY", "").strip() and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        missing.append("OPENAI_API_KEY or ANTHROPIC_API_KEY")

    if missing:
        raise RuntimeError(
            f"APP_ENV={APP_ENV} but required secrets are missing: {', '.join(missing)}. "
            f"See config/prod.env.example for the full list."
        )


def validate_config() -> None:
    """Validate dangerous runtime configuration values at startup.

    Catches misconfigurations that caused past incidents (see AGENTS.md
    root-cause table): unbounded T2 retries and excessive packaging timeouts.
    """
    from orchestrator.state import PipelineState

    cfg = get_config()
    state = PipelineState(request_id="", user_id="", prompt="")

    if not (0 <= state.max_t2_iterations <= 2):
        raise RuntimeError(
            f"max_t2_iterations must be between 0 and 2, got {state.max_t2_iterations}"
        )

    if cfg.zip_output_timeout <= 0 or cfg.zip_output_timeout >= 300:
        raise RuntimeError(
            f"ZIP_OUTPUT_TIMEOUT must be > 0 and < 300, got {cfg.zip_output_timeout}"
        )
