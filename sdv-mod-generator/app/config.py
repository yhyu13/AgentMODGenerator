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


def _safe_int(value: str | None, default: int) -> int:
    """Parse an integer from a string, falling back to default on error.

    Handles None, empty strings, non-numeric strings, and floats by
    returning the default value rather than raising.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


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
    zip_output_timeout: int = _safe_int(os.getenv("ZIP_OUTPUT_TIMEOUT", "120"), 120)
    # Admin-only destructive endpoints (e.g. ``POST /v1/mods/purge``)
    # gate their route on this flag so a misconfigured deployment
    # cannot bulk-delete ``mod_requests`` rows without an explicit
    # opt-in. Default ``False`` matches the branch's
    # ``discord-ops-hardening`` semantics; truthy parser accepts
    # ``1`` / ``true`` / ``yes`` case-insensitively (same vocabulary
    # as ``_safe_int`` and the existing ``Config`` boolean fields
    # — none today, but the pattern is documented for future ops
    # flags).
    admin_purge_enabled: bool = os.getenv("ADMIN_PURGE_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    # Retry endpoint gates (``POST /v1/mods/{request_id}/retry``).
    # ``retry_enabled`` defaults to ``False`` so test/dev defaults
    # to off — matches the v53 inline ``os.getenv("RETRY_ENABLED",
    # "false") != "true"`` behavior that the v108 refactor
    # supersedes. Truthy parser accepts ``1`` / ``true`` / ``yes``
    # case-insensitively (same vocabulary as ``admin_purge_enabled``
    # — keeps the operator-facing env-var surface uniform).
    retry_enabled: bool = os.getenv("RETRY_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    # Per-user retry counter cap (``retry_counter:{user_id}`` in
    # Redis, 24h TTL anchored on the first decrement of the day).
    # ``_safe_int`` handles malformed env values (non-numeric,
    # floats, empty string) by falling back to the supplied
    # default — same graceful-degrade pattern as
    # ``zip_output_timeout``. Default 5 matches the v53 inline
    # ``os.getenv("RETRY_MAX_PER_USER_PER_DAY", "5")`` with
    # ``try/except ValueError -> 5`` fallback.
    retry_max_per_user_per_day: int = _safe_int(
        os.getenv("RETRY_MAX_PER_USER_PER_DAY", "5"), 5
    )
    # T2 retry loop cap (per-request upper bound on
    # ``PipelineState.t2_iterations``). Wired into
    # ``orchestrator.pipeline.run_pipeline`` which reads the
    # singleton via ``get_config().max_t2_iterations`` and sets
    # it on the constructed ``PipelineState``. The pipeline
    # conditional ``if state.t2_iterations < state.max_t2_iterations``
    # is the loop guard — anything > 0 enables at least one
    # retry. ``_safe_int`` graceful-degrade handles malformed
    # env values identically to ``zip_output_timeout`` and
    # ``retry_max_per_user_per_day``. Default 0 matches the
    # pre-v109 hard-coded dataclass default (``PipelineState.
    # max_t2_iterations: int = 0``) and the P4.6 lesson in
    # RUNBOOK.md: bad LLM output + retries = infinite loop, so
    # the safe-by-default posture is "off" (T2 ships on first
    # iteration with feedback attached). Operators opt in via
    # ``MAX_T2_ITERATIONS=2`` in their env to enable retries.
    # ``validate_config()`` enforces ``0 <= max_t2_iterations <= 2``.
    max_t2_iterations: int = _safe_int(
        os.getenv("MAX_T2_ITERATIONS", "0"), 0
    )
    # v110 — boolean wrapper over the existing ``discord_bot_token``
    # string field that asks "is the Discord bot configured?". The
    # raw string field stays for backwards compat (existing call
    # sites in ``app/main.py`` and ``app/discord/*`` that read
    # ``cfg.discord_bot_token`` keep working unchanged). The bool
    # wrapper exists so startup checks + tests can express intent
    # at the right abstraction level ("is the bot configured?")
    # rather than checking the truthiness of a string. Strips the
    # value so a whitespace-only ``DISCORD_BOT_TOKEN="  "`` doesn't
    # trip the ``bool(os.getenv(...))`` truthiness trap (``bool("  ")
    # is True`` on Python 3.11+). Default ``False`` matches the
    # ``discord_bot_token`` default of ``""``. Consumed by
    # ``app.main.lifespan`` (v110 production warning when IS_PROD
    # and this is False) and ``tests/test_config_validation.py``
    # (v110 presence-check tests).
    discord_bot_configured: bool = bool(
        os.getenv("DISCORD_BOT_TOKEN", "").strip()
    )
    # v111 — boolean wrapper over the existing ``discord_app_id``
    # string field that asks "is the Discord app ID configured?".
    # Same shape as the v110 ``discord_bot_configured`` bool
    # wrapper: a thin ``bool(...strip())`` over the raw string
    # field so callers can express intent ("is the Discord app
    # ID set?") at the right abstraction level rather than
    # checking the truthiness of a string. Strips the value so
    # a whitespace-only ``DISCORD_APP_ID="  "`` doesn't trip the
    # ``bool(os.getenv(...))`` truthiness trap (``bool("  ") is
    # True`` on Python 3.11+). The raw string field stays for
    # backwards compat (``app/discord/bot.py:292`` reads
    # ``config.discord_app_id`` for a log line, unchanged).
    # Default ``False`` matches the ``discord_app_id`` default of
    # ``""``. Consumed by ``tests/test_config_validation.py`` (v111
    # presence-check tests). No production consumer today — the
    # field is purely a typed presence-check for the operator-facing
    # env-var surface; ``require_prod_secrets()`` already enforces
    # that ``DISCORD_APP_ID`` is non-empty in prod (it lives in
    # ``_REQUIRED_PROD_SECRETS``), so the prod path catches the
    # "missing app ID" case at startup. ``discord_app_id_valid``
    # is the dev / test / observability complement: it answers
    # "is the operator looking at a configured Discord app?"
    # without forcing a prod-only assertion.
    discord_app_id_valid: bool = bool(
        os.getenv("DISCORD_APP_ID", "").strip()
    )
    # v111 — boolean wrapper over the existing ``api_key`` string
    # field that asks "is the API key configured?". Same shape as
    # the v110 ``discord_bot_configured`` bool wrapper: a thin
    # ``bool(...strip())`` over the raw string field so callers
    # can express intent ("is the API key set?") at the right
    # abstraction level. Strips the value so a whitespace-only
    # ``API_KEY="  "`` doesn't trip the ``bool(os.getenv(...))``
    # truthiness trap (``bool("  ") is True`` on Python 3.11+).
    # The raw string field stays for backwards compat — every
    # existing call site (``app/api/routes.py:99-105`` where
    # ``verify_api_key`` reads ``cfg.api_key`` for the
    # ``secrets.compare_digest`` check; ``app/api/routes.py:3268``,
    # another ``if not cfg.api_key:`` gating branch) keeps working
    # unchanged. Default ``False`` matches the ``api_key`` default
    # of ``""``. Consumed by ``tests/test_config_validation.py``
    # (v111 presence-check tests). No production consumer today
    # beyond the existing ``verify_api_key`` helper which already
    # does the right thing (return 503 / False when ``API_KEY`` is
    # unset so callers know the server is misconfigured).
    # ``api_key_configured`` is the dev / test / observability
    # complement — lets tests + future health endpoints ask "is the
    # operator looking at a configured API key?" without forcing
    # a prod-only assertion.
    api_key_configured: bool = bool(
        os.getenv("API_KEY", "").strip()
    )
    # v113 — boolean wrapper over the existing ``api_owner_user_id``
    # string field that asks "is the API owner user ID configured?".
    # Same shape as the v111 ``api_key_configured`` bool wrapper: a
    # thin ``bool(...strip())`` over the raw string field so callers
    # can express intent ("is the owner user ID set?") at the right
    # abstraction level. Strips the value so a whitespace-only
    # ``API_OWNER_USER_ID="  "`` doesn't trip the
    # ``bool(os.getenv(...))`` truthiness trap (``bool("  ") is
    # True`` on Python 3.11+). The raw string field stays for
    # backwards compat — ``tests/test_get_history_endpoint.py``
    # reads ``cfg.api_owner_user_id`` to enforce the 403-on-mismatch
    # owner gate on the ``GET /v1/users/{id}/history`` endpoint
    # (when the field is set and the path's ``user_id`` doesn't
    # match, the handler returns 403; when the field is unset, the
    # owner gate is disabled and the endpoint returns the user's
    # history directly — dev-friendly default). Default ``False``
    # matches the ``api_owner_user_id`` default of ``""``. Consumed
    # by ``tests/test_config_validation.py`` (v113 presence-check
    # tests). No production consumer today beyond the existing
    # ``test_get_history_endpoint`` owner-gate check. ``api_owner_
    # configured`` is the dev / test / observability complement —
    # lets tests + future health endpoints ask "is the operator
    # looking at a configured API owner?" without forcing a
    # prod-only assertion.
    api_owner_configured: bool = bool(
        os.getenv("API_OWNER_USER_ID", "").strip()
    )


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
    cfg = get_config()

    # v109 — read ``max_t2_iterations`` from the config singleton
    # directly. The pre-v109 implementation constructed a throwaway
    # ``PipelineState(request_id="", user_id="", prompt="")`` and
    # read ``state.max_t2_iterations`` — which always evaluated to
    # the dataclass default of ``0``, making the check vacuous.
    # The cap now flows through ``Config.max_t2_iterations`` (parsed
    # from ``MAX_T2_ITERATIONS`` via ``_safe_int``), so this guard
    # finally exercises a real value: a misconfigured
    # ``MAX_T2_ITERATIONS=3`` will trip startup validation instead of
    # silently allowing an infinite T2 retry loop at runtime.
    max_t2 = cfg.max_t2_iterations
    if not (0 <= max_t2 <= 2):
        raise RuntimeError(
            f"max_t2_iterations must be between 0 and 2, got {max_t2}"
        )

    if cfg.zip_output_timeout <= 0 or cfg.zip_output_timeout >= 300:
        raise RuntimeError(
            f"ZIP_OUTPUT_TIMEOUT must be > 0 and < 300, got {cfg.zip_output_timeout}"
        )

    _VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if cfg.log_level.upper() not in _VALID_LOG_LEVELS:
        raise RuntimeError(
            f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got {cfg.log_level}"
        )

    if cfg.openai_model and not isinstance(cfg.openai_model, str):
        raise RuntimeError(f"OPENAI_MODEL must be a string, got {type(cfg.openai_model)}")
    if cfg.anthropic_model and not isinstance(cfg.anthropic_model, str):
        raise RuntimeError(f"ANTHROPIC_MODEL must be a string, got {type(cfg.anthropic_model)}")
