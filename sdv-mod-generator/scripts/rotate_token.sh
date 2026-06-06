#!/usr/bin/env bash
# Rotate the Discord bot token.
#
# Discord does not support programmatic token rotation — the operator must
# click "Reset Token" in the developer portal, then copy the new token into
# their secrets manager. This script:
#   1. Prints a checklist of what to do.
#   2. (Optional) Re-deploys the api service with the new env var injected
#      from a file written by the operator after they paste the new token.
#
# Usage:
#   ./scripts/rotate_token.sh                         # print checklist
#   ./scripts/rotate_token.sh /run/secrets/discord    # apply token from file
#   ./scripts/rotate_token.sh --restart-container     # re-deploy api service
#
# The deploy target defaults to "docker compose -f docker-compose.prod.yml".
set -euo pipefail

SECRETS_FILE="${1:-}"
RESTART=0
for arg in "$@"; do
    if [ "$arg" = "--restart-container" ]; then
        RESTART=1
    fi
done

cat <<'CHECKLIST'
== Discord token rotation ==

1. Open https://discord.com/developers/applications
2. Select the bot application.
3. Bot → "Reset Token" → confirm.
4. Copy the new token.
5. Update your secrets manager:
     - AWS:   aws secretsmanager put-secret-value --secret-id sdv-mod/discord-bot-token --secret-string "<NEW_TOKEN>"
     - GCP:   gcloud secrets versions add discord-bot-token --data-file=-
     - Vault: vault kv put secret/sdv-mod/discord token=<NEW_TOKEN>
     - File:  write to /run/secrets/discord on the host (chmod 0400, root only)
6. Re-deploy / restart the api container (see --restart-container).
7. Tail logs and confirm:  discord.bot.ready  user=<bot-name>
8. Revoke the old token from any other systems that used it.
CHECKLIST

if [ -z "$SECRETS_FILE" ]; then
    exit 0
fi

if [ ! -r "$SECRETS_FILE" ]; then
    echo "error: cannot read $SECRETS_FILE" >&2
    exit 1
fi

NEW_TOKEN="$(tr -d '[:space:]' < "$SECRETS_FILE")"
if [ -z "$NEW_TOKEN" ]; then
    echo "error: $SECRETS_FILE is empty" >&2
    exit 1
fi

if ! command -v grep >/dev/null; then
    echo "error: grep not found" >&2
    exit 1
fi

# Discord tokens are three base64-url segments separated by dots, last
# segment is the checksum. We sanity-check the shape but do not validate
# the checksum (we don't have the public key here).
if ! echo "$NEW_TOKEN" | grep -Eq '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'; then
    echo "warning: token does not match expected Discord shape — proceeding anyway" >&2
fi

echo "Token read from $SECRETS_FILE (length=${#NEW_TOKEN})."

if [ "$RESTART" -eq 1 ]; then
    if command -v docker >/dev/null 2>&1; then
        echo "Restarting api service with the new token..."
        DISCORD_BOT_TOKEN="$NEW_TOKEN" docker compose -f docker-compose.prod.yml up -d --no-deps api
        echo "Restart complete. Tail logs: docker compose -f docker-compose.prod.yml logs -f api"
    else
        echo "error: --restart-container requested but docker is not on PATH" >&2
        exit 1
    fi
fi
