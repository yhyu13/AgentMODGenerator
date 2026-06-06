#!/usr/bin/env bash
# End-to-end SDV runtime smoke test.
#
# This is the gate P5.5 requires before opening the bot to users. It
# exercises the FULL pipeline (API -> generators -> packaging -> zip) and
# then drops the result into a real Stardew Valley install so SMAPI loads
# it. If SMAPI reports an error or "this mod failed to load" in the
# console log, the script exits non-zero and the deploy is considered
# failed. There is no static check that can stand in for this — see the
# precondition on P5 in AGENTS.md.
#
# Usage:
#   SDV_INSTALL_PATH=/path/to/Stardew\ Valley \
#     ./scripts/sdv_smoke_test.sh
#
# Or with a prebuilt test zip:
#   SDV_INSTALL_PATH=/path/to/sdv TEST_ZIP=/path/to/Mod.zip ./scripts/sdv_smoke_test.sh
#
# Environment variables:
#   SDV_INSTALL_PATH   Absolute path to the Stardew Valley install dir
#                      (must contain a `Mods/` folder and the SMAPI
#                      binary). REQUIRED.
#   API_BASE           Base URL of the running API (default: http://localhost:8000)
#   TEST_PROMPT        Prompt to send to /v1/mods/generate if no TEST_ZIP
#                      (default: "make a TV shopping channel that sells seeds on Sundays")
#   SMAPI_TIMEOUT      Max seconds to wait for SMAPI to write its log
#                      (default: 90)
#   SMAPI_BIN          Override path to the SMAPI launcher
#                      (default: $SDV_INSTALL_PATH/StardewModdingAPI on Linux/macOS,
#                                $SDV_INSTALL_PATH/StardewModdingAPI.exe on Windows)
set -euo pipefail

SDV_INSTALL_PATH="${SDV_INSTALL_PATH:-}"
API_BASE="${API_BASE:-http://localhost:8000}"
TEST_PROMPT="${TEST_PROMPT:-make a TV shopping channel that sells seeds on Sundays}"
SMAPI_TIMEOUT="${SMAPI_TIMEOUT:-90}"
TEST_ZIP="${TEST_ZIP:-}"

if [ -z "$SDV_INSTALL_PATH" ]; then
    echo "SKIP: SDV_INSTALL_PATH is not set." >&2
    echo "      This host has no Stardew Valley install, which is expected" >&2
    echo "      on a CI build agent. The smoke test only runs on deploy" >&2
    echo "      hosts that have SDV installed (per AGENTS.md P5 precondition)." >&2
    exit 0
fi

if [ ! -d "$SDV_INSTALL_PATH" ]; then
    echo "FAIL: SDV_INSTALL_PATH does not exist or is not a directory: $SDV_INSTALL_PATH" >&2
    exit 1
fi

MODS_DIR="$SDV_INSTALL_PATH/Mods"
if [ ! -d "$MODS_DIR" ]; then
    echo "FAIL: $MODS_DIR does not exist. Is SMAPI installed?" >&2
    exit 1
fi

# Pick the SMAPI launcher
if [ -n "${SMAPI_BIN:-}" ]; then
    :
elif [ -x "$SDV_INSTALL_PATH/StardewModdingAPI.exe" ]; then
    SMAPI_BIN="$SDV_INSTALL_PATH/StardewModdingAPI.exe"
elif [ -x "$SDV_INSTALL_PATH/StardewModdingAPI" ]; then
    SMAPI_BIN="$SDV_INSTALL_PATH/StardewModdingAPI"
else
    echo "FAIL: could not find StardewModdingAPI[.exe] in $SDV_INSTALL_PATH" >&2
    echo "      Install SMAPI first: https://smapi.io/" >&2
    exit 1
fi

# Pick a clean working area for the test
WORK_DIR="$(mktemp -d -t sdv-smoke-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
SMAPI_LOG="$WORK_DIR/SMAPI-latest.txt"
TEST_MOD_DIR="$MODS_DIR/AgentModSmokeTest"

if [ -n "$TEST_ZIP" ]; then
    if [ ! -r "$TEST_ZIP" ]; then
        echo "FAIL: TEST_ZIP is not readable: $TEST_ZIP" >&2
        exit 1
    fi
    cp "$TEST_ZIP" "$WORK_DIR/test.zip"
else
    echo "==> generating test mod via $API_BASE"
    # Wait for /health/deep before generating
    if ! curl -fsS "$API_BASE/health/deep" >/dev/null; then
        echo "FAIL: $API_BASE/health/deep is not ready" >&2
        exit 1
    fi

    GEN_RESPONSE="$(curl -fsS -X POST "$API_BASE/v1/mods/generate" \
        -H 'Content-Type: application/json' \
        -d "{\"user_id\":\"smoke_test\",\"prompt\":\"$TEST_PROMPT\"}")" \
        || { echo "FAIL: /v1/mods/generate request failed" >&2; exit 1; }
    REQUEST_ID="$(printf '%s' "$GEN_RESPONSE" | sed -n 's/.*"request_id":"\([^"]*\)".*/\1/p')"
    if [ -z "$REQUEST_ID" ]; then
        echo "FAIL: no request_id in response: $GEN_RESPONSE" >&2
        exit 1
    fi
    echo "    request_id=$REQUEST_ID"

    # Poll status up to 4 minutes
    POLL_DEADLINE=$(( $(date +%s) + 240 ))
    while :; do
        if [ "$(date +%s)" -ge "$POLL_DEADLINE" ]; then
            echo "FAIL: pipeline did not finish within 4 minutes" >&2
            exit 1
        fi
        STATUS="$(curl -fsS "$API_BASE/v1/mods/status/$REQUEST_ID" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
        case "$STATUS" in
            done) break ;;
            failed) echo "FAIL: pipeline reported failed for $REQUEST_ID" >&2; exit 1 ;;
            *) sleep 3 ;;
        esac
    done

    DL_RESPONSE="$(curl -fsS "$API_BASE/v1/mods/download/$REQUEST_ID")" \
        || { echo "FAIL: /v1/mods/download request failed" >&2; exit 1; }
    DOWNLOAD_URL="$(printf '%s' "$DL_RESPONSE" | sed -n 's/.*"download_url":"\([^"]*\)".*/\1/p')"
    if [ -z "$DOWNLOAD_URL" ]; then
        echo "FAIL: no download_url in response: $DL_RESPONSE" >&2
        exit 1
    fi

    case "$DOWNLOAD_URL" in
        file://*) cp "${DOWNLOAD_URL#file://}" "$WORK_DIR/test.zip" ;;
        http*)    curl -fsSL -o "$WORK_DIR/test.zip" "$DOWNLOAD_URL" ;;
        *)
            echo "FAIL: unsupported download URL: $DOWNLOAD_URL" >&2
            exit 1
            ;;
    esac
fi

# Unzip into the Mods/ directory
rm -rf "$TEST_MOD_DIR"
mkdir -p "$TEST_MOD_DIR"
unzip -q -o "$WORK_DIR/test.zip" -d "$TEST_MOD_DIR"

# Clean any old SMAPI log so we only inspect this run
find "$SDV_INSTALL_PATH" -maxdepth 2 -name 'SMAPI-latest.txt' -delete 2>/dev/null || true

# Launch SMAPI. Many setups (Steam, Proton, X11 forwarding) won't work in
# a headless deploy environment, so this script is intended for the
# "production host with a real GPU + SDV install" case described in
# AGENTS.md. On other hosts the operator sets SDV_INSTALL_PATH to skip.
echo "==> launching SMAPI ($SMAPI_BIN), waiting up to ${SMAPI_TIMEOUT}s for log"
DEADLINE=$(( $(date +%s) + SMAPI_TIMEOUT ))
LAUNCH_STATUS=0
if [ "${OS:-}" = "Windows_NT" ] || [[ "$SMAPI_BIN" == *.exe ]]; then
    "$SMAPI_BIN" >/dev/null 2>&1 &
else
    "$SMAPI_BIN" >/dev/null 2>&1 &
fi
LAUNCH_PID=$!

# Wait for SMAPI to write its log
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if [ -r "$SDV_INSTALL_PATH/SMAPI-latest.txt" ] || \
       [ -r "$SDV_INSTALL_PATH/smapi-internal/SMAPI-latest.txt" ]; then
        break
    fi
    if ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
        LAUNCH_STATUS=1
        break
    fi
    sleep 1
done

# Best-effort terminate the game process
kill "$LAUNCH_PID" 2>/dev/null || true
wait "$LAUNCH_PID" 2>/dev/null || true

SMAPI_LOG_FILE=""
[ -r "$SDV_INSTALL_PATH/SMAPI-latest.txt" ] && SMAPI_LOG_FILE="$SDV_INSTALL_PATH/SMAPI-latest.txt"
[ -z "$SMAPI_LOG_FILE" ] && [ -r "$SDV_INSTALL_PATH/smapi-internal/SMAPI-latest.txt" ] && SMAPI_LOG_FILE="$SDV_INSTALL_PATH/smapi-internal/SMAPI-latest.txt"

if [ -z "$SMAPI_LOG_FILE" ]; then
    echo "FAIL: SMAPI did not write a log within ${SMAPI_TIMEOUT}s (launch_status=$LAUNCH_STATUS)" >&2
    exit 1
fi
cp "$SMAPI_LOG_FILE" "$SMAPI_LOG"
echo "==> SMAPI log: $SMAPI_LOG"

# Grep for failure indicators
FAIL_PATTERNS=(
    'this mod failed'
    'this mod could not be loaded'
    'error loading mod'
    'agentmodsmoketest.*error'
    'agentmodsmoketest.*exception'
)
for p in "${FAIL_PATTERNS[@]}"; do
    if grep -Ei "$p" "$SMAPI_LOG" >/dev/null 2>&1; then
        echo "FAIL: SMAPI log contains '$p':" >&2
        grep -Ei -A2 "$p" "$SMAPI_LOG" >&2 || true
        exit 1
    fi
done

echo "OK: SDV loaded the test mod cleanly"
exit 0
