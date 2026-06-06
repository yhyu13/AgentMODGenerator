#!/usr/bin/env bash
# Bring up the full prod stack on a fresh host using docker-compose.prod.yml.
#
# Usage:
#   cp config/prod.env.example config/prod.env
#   $EDITOR config/prod.env          # fill in real values
#   set -a; source config/prod.env; set +a
#   make deploy-local
#
# This script will refuse to start if APP_ENV!=prod or if any required
# env var is empty. See config/prod.env.example for the full list.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${APP_ENV:-}" != "prod" ] && [ "${APP_ENV:-}" != "production" ]; then
    echo "error: APP_ENV must be 'prod' to deploy with this script (got '${APP_ENV:-}')" >&2
    exit 1
fi

REQUIRED=(
    DATABASE_URL
    REDIS_URL
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    DISCORD_BOT_TOKEN
    API_KEY
    POSTGRES_PASSWORD
    MINIO_ROOT_USER
    MINIO_ROOT_PASSWORD
)
MISSING=()
for v in "${REQUIRED[@]}"; do
    if [ -z "${!v:-}" ]; then
        MISSING+=("$v")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "error: required env vars are unset: ${MISSING[*]}" >&2
    echo "       source them from your secrets manager, then re-run." >&2
    exit 1
fi

# Refuse to start if the operator accidentally leaked a plaintext secret
# to a file in this repo (defence in depth).
if [ -r config/.env ]; then
    if grep -EHn 'DISCORD_BOT_TOKEN=[A-Za-z0-9_.-]{20,}' config/.env >/dev/null 2>&1; then
        echo "error: config/.env contains a plaintext DISCORD_BOT_TOKEN." >&2
        echo "       Move secrets to your secrets manager (see docs/RUNBOOK.md)." >&2
        exit 1
    fi
fi

echo "==> building image"
docker compose -f config/docker-compose.prod.yml build api

echo "==> starting stack"
docker compose -f config/docker-compose.prod.yml up -d

echo "==> waiting for /health/deep"
ATTEMPTS=0
MAX_ATTEMPTS=40
until curl -fsS http://localhost:${API_PORT:-8000}/health/deep >/dev/null 2>&1; do
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
        echo "error: /health/deep did not become ready within $((MAX_ATTEMPTS * 5))s" >&2
        echo "       logs:" >&2
        docker compose -f config/docker-compose.prod.yml logs --tail=200 api >&2
        exit 1
    fi
    sleep 5
done

echo "==> stack is up"
echo "    API:    http://localhost:${API_PORT:-8000}"
echo "    Health: http://localhost:${API_PORT:-8000}/health/deep"
echo "    Logs:   docker compose -f config/docker-compose.prod.yml logs -f api"

# Post-deploy smoke test — only runs if SDV_INSTALL_PATH is set. On a CI
# or staging host without SDV, this silently skips (exit 0). On a host
# with SDV installed, the deploy fails if SMAPI can't load the test mod.
if [ -n "${SDV_INSTALL_PATH:-}" ]; then
    echo "==> running SDV runtime smoke test against $SDV_INSTALL_PATH"
    API_BASE="http://localhost:${API_PORT:-8000}" \
        SDV_INSTALL_PATH="$SDV_INSTALL_PATH" \
        ./scripts/sdv_smoke_test.sh
else
    echo "==> SDV_INSTALL_PATH unset — skipping runtime smoke test"
    echo "    To open the bot to users, set SDV_INSTALL_PATH and re-run"
    echo "    'make deploy' on a host that has Stardew Valley installed."
fi
