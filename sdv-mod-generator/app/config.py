"""Environment configuration — loads from .env."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

_dotenv_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(_dotenv_path, override=True)


@dataclass
class Config:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_base_url: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost:5432/sdv_mods")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    s3_bucket: str = os.getenv("S3_BUCKET", "sdv-mod-generator")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")
    discord_app_id: str = os.getenv("DISCORD_APP_ID", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    api_key: str = os.getenv("API_KEY", "")


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
