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
    local_output_dir: str = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")
    discord_app_id: str = os.getenv("DISCORD_APP_ID", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    api_key: str = os.getenv("API_KEY", "")
    api_owner_user_id: str = os.getenv("API_OWNER_USER_ID", "")


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def require_prod_secrets() -> None:
    """Validate that the secrets a production deploy must have are set.

    Called once at startup when APP_ENV=prod. Aborts the process (raises)
    rather than running with a missing or default secret. This is the
    "fail closed" behaviour P5.1 calls for: a misconfigured prod deploy
    does not silently fall back to dev defaults.

    Reads os.environ directly (not the cached Config) so the test suite
    can patch env vars per-test without invalidating the module cache.
    """
    required_prod = {
        "DATABASE_URL": os.getenv("DATABASE_URL", "").strip(),
        "REDIS_URL": os.getenv("REDIS_URL", "").strip(),
        "DISCORD_BOT_TOKEN": os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        "API_KEY": os.getenv("API_KEY", "").strip(),
    }
    missing = [k for k, v in required_prod.items() if not v]
    if missing:
        raise RuntimeError(
            f"APP_ENV=prod but required env vars are empty: {missing}. "
            f"Source them from your secrets manager (see config/prod.env.example)."
        )
    if required_prod["DATABASE_URL"].startswith(
        "postgresql+asyncpg://postgres:postgres@localhost"
    ):
        raise RuntimeError(
            "APP_ENV=prod but DATABASE_URL points at the dev default. "
            "Set DATABASE_URL to the production database."
        )
