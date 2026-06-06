# SDV Mod Generator — Production Runbook

This runbook is what a new on-call reads first when they get paged. Every
section ends with the literal command (or URL) to run. There is no
context-switch to a wiki or an internal tool.

## At a glance

| Thing | Where |
|---|---|
| API base URL | `https://<your-host>` (configurable via `API_PORT` and reverse proxy) |
| Health (liveness) | `GET /health` |
| Health (deep, every dep) | `GET /health/deep` |
| Prometheus metrics | `GET /metrics` |
| Logs (JSON to stdout) | `docker compose -f config/docker-compose.prod.yml logs -f api` |
| Stack bring-up | `make deploy-local` (or `make deploy` if SDV is installed on the host) |
| Stack teardown | `make deploy-stop` |
| Token rotation | `make rotate-token TOKEN_FILE=/run/secrets/discord` |
| Secrets audit | `make check-no-plaintext-secrets FILE=/etc/sdv-mod-generator/env` |
| Smoke test | `SDV_INSTALL_PATH=… make smoke-test` |

## "The bot is down" — 10-minute triage

1. **Is the process alive?**
   ```sh
   curl -fsS https://<host>/health
   ```
   If this 200s, the API is up. If it 5xx's, the process is dead — go
   straight to step 4 (logs).

2. **Are dependencies reachable?**
   ```sh
   curl -fsS https://<host>/health/deep
   ```
   Returns 200 with `{"status":"ok"}` when all of: postgres, redis, S3
   (MinIO), and the Discord gateway are up. A 503 response includes a
   per-dependency `{"ok": false, "error": "..."}` body — read the error
   to know which one is down.

3. **Has the bot reached the Discord gateway?**
   Look for the `discord.bot.ready` event in the logs. If you see
   `discord.bot.start.failed` or no `ready` event after 60s, the token
   may be wrong. Jump to **Token rotation** below.

4. **Tail the logs while reproducing.**
   ```sh
   docker compose -f config/docker-compose.prod.yml logs -f api
   ```
   Logs are JSON. Pipe through `jq` for readable output:
   ```sh
   docker compose -f config/docker-compose.prod.yml logs -f api | jq -c .
   ```

5. **Find a specific user request.**
   Every log line carries a `request_id` (mirrors the `X-Request-ID`
   header). The end-user may have one. Filter:
   ```sh
   docker compose logs api | jq -c "select(.request_id == \"req_abc123\")"
   ```

## Health & metrics endpoints

### `/health` (liveness)

Cheap. Returns `{status, ts, discord_bot_ready}`. 200 unless the process
is so broken it can't respond. Used by k8s `livenessProbe` and Docker
`HEALTHCHECK`.

### `/health/deep` (readiness)

Pings postgres (`SELECT 1`), redis (`PING`), S3 (`head_bucket` on
`$S3_BUCKET`), and the Discord gateway (`is_bot_ready()` + latency).
Returns 200 with `status: ok` when all are up; 503 with `status:
degraded` and a `checks` array otherwise. The `sdv_dependency_up{dependency=...}`
gauge is updated on every call.

### `/metrics` (Prometheus)

Counter / histogram metrics for scraping:

- `sdv_api_requests_total{method,path,status}` — request count
- `sdv_api_request_duration_seconds{method,path}` — latency histogram
- `sdv_pipeline_runs_total{status}` — pipeline runs by terminal status
- `sdv_pipeline_t2_score` — T2 judge panel score distribution
- `sdv_pipeline_generators_failed_total{generator}` — per-generator failure count
- `sdv_pipeline_generators_succeeded_total{generator}` — per-generator success count
- `sdv_dependency_up{dependency}` — 1 if /health/deep last saw it up, 0 otherwise

Path labels are route templates (e.g. `/v1/mods/{request_id}`), not raw
URLs, so cardinality is bounded.

A starter dashboard query (Grafana / Prometheus):

```promql
# requests per second, by status
sum by (status) (rate(sdv_api_requests_total[5m]))

# p99 latency for /v1/mods/generate
histogram_quantile(0.99,
  sum by (le) (rate(sdv_api_request_duration_seconds_bucket{path="/v1/mods/generate"}[5m])))

# pipeline success rate (last hour)
sum(rate(sdv_pipeline_runs_total{status="done"}[1h]))
  / sum(rate(sdv_pipeline_runs_total[1h]))

# dependency health
min(sdv_dependency_up) by (dependency)
```

## Reading `t2_score`

T2 is the 3-judge LLM panel. Each judge scores a mod 0–10 on plausibility
+ correctness. The pipeline stores:

- `t2_score` — sum of the three judge scores (0–30)
- `t2_panel_passed_count` — number of judges with score ≥ 7 (threshold)
- `t2_passed` — true if at least 2 of 3 judges scored ≥ 7
- `t2_feedback` — concatenated judge feedback, surfaced to the user when the gate fails
- `t2_max_score` — always 30 (three judges × 10 max)
- `t2_pass_threshold` — always 7 per judge (2-of-3 majority)

How to interpret a single number:

| `t2_score` | Meaning |
|---|---|
| 27–30 | Strongly passing (all 3 judges ≥ 9) — happy path |
| 22–26 | Passing (≥2 judges ≥ 7) — ship as-is |
| 18–21 | Borderline — usually 1 judge passes, 2 don't. Read `t2_feedback` to decide whether to ship or regenerate. |
| 0–17 | Failing (0–1 judges pass). Pipeline retries up to `max_t2_iterations`, then ships anyway with the feedback attached. |

`max_t2_iterations` is set to 0 by default (P4.6 lesson: bad LLM output +
retry = infinite loop). When the LLM is consistently broken, you will
see `pipeline.t2.max_iterations` in the logs and `t2_passed=false` in the
response — the mod ships but is flagged.

## Logs

Every line is one JSON object on stdout. The 12-factor contract: the
container does not write to disk, your log shipper (Loki, CloudWatch,
Vector, etc.) tails the container's stdout and forwards it.

Field reference (all required unless marked optional):

| Field | Type | Example | Notes |
|---|---|---|---|
| `timestamp` | string (ISO-8601 UTC) | `2026-06-06T09:30:23.688Z` | |
| `level` | string | `info` | `info`/`warning`/`error`/`debug` |
| `event` | string | `pipeline.done` | dot.case — grep by this |
| `logger` | string | `orchestrator.pipeline` | Python logger name |
| `request_id` | string | `req_abc123def456` | optional, set per HTTP request |
| …all kwargs | | | arbitrary structured fields the caller passed |

Two queries you'll run:

```sh
# All errors in the last hour
docker compose logs --since 1h api | jq -c 'select(.level == "error")'

# Everything for one request
docker compose logs api | jq -c "select(.request_id == \"$REQ_ID\")"
```

## Rollback

The stack is single-replica (per P5 scope — no horizontal scaling). To
roll back to the previous release:

```sh
# Tag the deploy first
docker compose -f config/docker-compose.prod.yml pull api || true

# Roll back to the previous image tag
SDV_MOD_GENERATOR_IMAGE=<previous-tag> docker compose -f config/docker-compose.prod.yml up -d

# Confirm
curl -fsS https://<host>/health/deep
```

If the previous image is already gone from your registry, restore from
the `phase5-deploy` branch in the repo, rebuild, and redeploy:

```sh
git checkout phase5-deploy
make deploy-local
```

## Token rotation (Discord)

Required when the token is leaked, when a developer with bot access
leaves, or on a regular schedule (90 days recommended).

1. Reset the token in the Discord developer portal.
2. Write the new token to a file with `chmod 0400`:
   ```sh
   umask 077
   echo -n "NEW_TOKEN_HERE" > /run/secrets/discord
   ```
3. Update your secrets manager (one of):
   - **AWS**: `aws secretsmanager put-secret-value --secret-id sdv-mod/discord-bot-token --secret-string "$(cat /run/secrets/discord)"`
   - **GCP**: `gcloud secrets versions add discord-bot-token --data-file=/run/secrets/discord`
   - **Vault**: `vault kv put secret/sdv-mod/discord token=$(cat /run/secrets/discord)`
4. Restart the api container:
   ```sh
   DISCORD_BOT_TOKEN=$(cat /run/secrets/discord) docker compose -f config/docker-compose.prod.yml up -d --no-deps api
   ```
   Or use `make rotate-token TOKEN_FILE=/run/secrets/discord` (calls
   the helper script).
5. Confirm the new token took effect:
   ```sh
   docker compose logs -f api | jq -c 'select(.event == "discord.bot.ready")'
   ```
6. Revoke the old token from any other system that used it.

## Secrets audit

Run on any host to confirm no plaintext secret leaked to disk:

```sh
make check-no-plaintext-secrets FILE=/etc/sdv-mod-generator/env
```

The script greps for known secret shapes (Discord tokens, OpenAI keys,
Anthropic keys, AWS secret keys, API keys) and exits non-zero on match.
Run after every rotation and after every deploy.

## Where do the generated zips live?

- Local mode (no `AWS_ACCESS_KEY_ID`): `$LOCAL_OUTPUT_DIR` on the
  container's filesystem — `docker volume inspect sdv-mod-generator_app_outputs`.
- S3/MinIO mode: the bucket named by `S3_BUCKET` (default
  `sdv-mod-generator`). The presigned URL in the API response expires
  in 1 hour by default.

## Common failure modes (from the AGENTS.md root-cause table)

| Symptom | Likely cause | What to do |
|---|---|---|
| `/health/deep` returns 503 with `discord_bot: ok=false, error=bot_not_started` | Discord token invalid or SOCKS proxy misconfigured | Rotate token; check `discord.bot.start.failed` event in logs |
| Pipeline never finishes (status stuck on `running`) | Background task not scheduled (uvicorn + asyncio.create_task issue from AGENTS.md P4.6) | Restart; verify with `docker compose logs api \| grep pipeline.background_started` |
| All generators fail with `no LLM provider configured` | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` empty | Set the env var and restart |
| T2 panel always returns 0 | LLM down or rate-limited | `docker compose logs api \| grep t2` to see raw errors |
| `make deploy-local` refuses to start | `APP_ENV` not set to `prod`, or a required env var is empty | `set -a; source config/prod.env; set +a` and re-run |
| Smoke test fails with `this mod failed` in SMAPI log | Generated zip is structurally invalid (rare — T1 gate should catch this) | `docker compose logs api \| grep -i 't1\\|gate'` — T1 errors are surfaced as `t1_errors` in `/v1/mods/{id}` |
