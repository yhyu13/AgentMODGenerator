#!/usr/bin/env bash
# Fail if any plaintext secret-shaped value is found in the given env file.
#
# This is the "is the live token committed to disk on the host?" check
# P5.1 calls for. Run it on a prod host to confirm a token rotation
# actually moved the secret out of plaintext (e.g. into a secrets manager
# that injects at startup, never writing to a file).
#
# Usage:
#   ./scripts/check_no_plaintext_secrets.sh /etc/sdv-mod-generator/env
#   ./scripts/check_no_plaintext_secrets.sh config/.env
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <env-file>" >&2
    exit 2
fi

FILE="$1"
if [ ! -r "$FILE" ]; then
    echo "error: cannot read $FILE" >&2
    exit 2
fi

# Patterns that, if present in a file, indicate a plaintext secret is on
# disk. Add new patterns as more secret types are introduced.
PATTERNS=(
    'DISCORD_BOT_TOKEN=[A-Za-z0-9_.-]{20,}'
    'OPENAI_API_KEY=sk-[A-Za-z0-9_-]{8,}'
    'ANTHROPIC_API_KEY=sk-ant-[A-Za-z0-9_-]{8,}'
    'AWS_SECRET_ACCESS_KEY=[A-Za-z0-9/+=]{30,}'
    'API_KEY=[A-Za-z0-9_-]{20,}'
)

FOUND=0
for p in "${PATTERNS[@]}"; do
    if grep -EHn "$p" "$FILE" >/dev/null 2>&1; then
        echo "FAIL: $FILE contains a plaintext secret matching: $p" >&2
        grep -EHn "$p" "$FILE" >&2 || true
        FOUND=1
    fi
done

if [ "$FOUND" -eq 1 ]; then
    echo "FAIL: move secrets to a secrets manager (see docs/RUNBOOK.md)." >&2
    exit 1
fi

echo "OK: no plaintext secrets in $FILE"
