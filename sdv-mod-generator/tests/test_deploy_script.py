"""Tests for P5.2 deploy script — verify it refuses to run with bad config."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_local.sh"
COMPOSE_FILE = REPO_ROOT / "config" / "docker-compose.prod.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_deploy_script_exists_and_is_executable() -> None:
    assert DEPLOY_SCRIPT.exists()
    assert os.access(DEPLOY_SCRIPT, os.X_OK)


def test_compose_prod_file_exists() -> None:
    assert COMPOSE_FILE.exists()
    text = COMPOSE_FILE.read_text()
    for svc in ("postgres", "redis", "minio", "minio-init", "api"):
        assert f"  {svc}:" in text, f"service {svc!r} missing from docker-compose.prod.yml"


def test_compose_prod_uses_minio_for_s3() -> None:
    text = COMPOSE_FILE.read_text()
    assert "minio" in text
    # S3 endpoint should default to minio
    assert "S3_ENDPOINT_URL" in text
    assert "minio:9000" in text


def test_dockerfile_has_healthcheck() -> None:
    text = DOCKERFILE.read_text()
    assert "HEALTHCHECK" in text
    assert "/health/deep" in text
    assert "EXPOSE 8000" in text
    assert "APP_ENV=prod" in text


def test_deploy_script_refuses_non_prod_env() -> None:
    """deploy_local.sh must refuse to start with APP_ENV=dev."""
    env = {
        "APP_ENV": "dev",
        "PATH": os.environ["PATH"],
    }
    result = subprocess.run(
        [str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "APP_ENV" in result.stderr


def test_deploy_script_refuses_missing_required_secrets() -> None:
    """With APP_ENV=prod but no required env, deploy_local.sh must abort."""
    env = {
        "APP_ENV": "prod",
        "PATH": os.environ["PATH"],
    }
    result = subprocess.run(
        [str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "required env vars" in result.stderr or "DATABASE_URL" in result.stderr
