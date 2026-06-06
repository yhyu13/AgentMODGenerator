# AGENTS.md — SDV Mod Generator

## Project Status

**Active project** — Phases 0-4 complete. Discord bot, API, and mod generation pipeline are running.

Current state:
- `POST /v1/mods/generate` — non-blocking, returns request_id + status="running"
- `GET /v1/mods/status/{id}` — polls Redis for status
- `GET /v1/mods/download/{id}` — presigned S3 URL
- Pipeline runs 11 generators → T1 gate → T2 3-judge panel → zip packaging

---

## What This Project Does

AI generates complete Stardew Valley Content Patcher mods from a single user prompt (e.g., "make a TV shopping channel"). User sends request via Discord or API → AI generates zip → delivered back to user.

## Architecture

```
FastAPI → LangGraph orchestrator (Route → Generate → T1 Gate → T2 Gate (3-judge panel) → Package)
         ↓
       S3 (zip storage) + PostgreSQL (state) + Redis (status cache)
```

## Build Phases

| Phase | Goal | Status |
|-------|------|--------|
| P0 | End-to-end, zip out | ✅ Done |
| P1 | API layer + status polling | ✅ Done |
| P2 | Discord bot | ✅ Done |
| P3 | More generators (FeedbackRouter) | ✅ Done |
| P4 | Testing + quality gates + Discord webhook | ✅ Done |
| P5 | Deploy + monitoring | TODO |

---

## Agent Workflow (Lessons from Git History)

### Phase Implementation Workflow

```bash
# 1. Pre-work — confirm on current master
git fetch origin
git status

# 2. Create worktree from current master
git worktree add ../project-phase{N} -b phase{N}-{name}

# 3. Implement in worktree (via agent)
#    - Run syntax check after each file
#    - Run tests before commit

# 4. Commit with descriptive message

# 5. Merge to master

# 6. Post-merge
make test              # Full test suite
curl /health          # Verify server
git worktree remove   # Delete worktree immediately
git branch -d         # Delete phase branch
```

### Worktree Checklist (REQUIRED)

Before creating a worktree:
- [ ] `git branch master && git log -1` — confirm base is current master
- [ ] `git fetch origin` — ensure origin is up to date

After merging:
- [ ] Delete worktree immediately: `git worktree remove ../project-phase{N}`
- [ ] Delete branch: `git branch -d phase{N}-{name}`

**Stale worktree prevention:**
```bash
# Check for stale worktrees
git worktree list

# Remove stale worktrees (branch already merged)
git worktree list | grep -v "(main)" | awk '{print $1}' | xargs -r git worktree remove
```

### Debugging Checklist (REQUIRED before deep dive)

When something isn't working:

1. [ ] Check recent commits to modified files: `git log --oneline -3 -- <file>`
2. [ ] Check configuration values: `grep -r "max_t2_iterations\|timeout\|retry" sdv-mod-generator/`
3. [ ] Run with timeout: `timeout 60 python test.py`
4. [ ] Verify Redis/DB connectivity: `redis-cli ping`
5. [ ] Check logs for "pipeline.background_started" if background task issue

**Anti-pattern observed:** Agent tried 5+ different async patterns without checking the obvious (configuration causing infinite loop).

### Configuration Validation (Startup)

```python
# On application startup — validate dangerous configs
def validate_config():
    from orchestrator.state import PipelineState
    state = PipelineState()
    assert 0 <= state.max_t2_iterations < 3, "max_t2_iterations must be 0-2"
    assert state.zip_output_timeout < 300, "timeout too high"
```

---

## Project Layout

```
sdv-mod-generator/
├── app/
│   ├── main.py              # FastAPI app + lifespan
│   ├── config.py            # Config from env
│   └── api/
│       ├── routes.py        # /v1/mods/* endpoints
│       └── schemas.py       # Pydantic schemas
│   └── discord/
│       ├── bot.py           # Discord bot + /generate command
│       ├── webhook.py       # Completion notifications
│       └── connector.py     # Bot connection handling
├── orchestrator/
│   ├── pipeline.py          # LangGraph pipeline + nodes
│   ├── state.py            # PipelineState dataclass
│   ├── router.py           # Prompt → generators routing
│   └── feedback_router.py   # T2 feedback → generator routing
├── generators/
│   ├── base.py             # BaseGenerator, GeneratorOutput
│   ├── core/
│   │   └── base.py         # GeneratorInput dataclass
│   ├── packs/
│   │   └── stardew_valley/
│   │       ├── __init__.py  # GamePack registration
│   │       └── features/
│   │           └── shop_channel/
│   │               └── __init__.py  # 11 shop_channel generators
│   └── packager.py         # ZIP creation
├── quality/
│   ├── gate_t1.py          # Deterministic schema checks
│   └── gate_t2.py          # 3-judge LLM panel
├── storage/
│   ├── postgres.py         # Async SQLAlchemy
│   ├── redis.py           # Async redis client
│   ├── s3.py              # S3 + local fallback
│   ├── queries.py          # DB queries
│   └── models/             # SQLAlchemy models
├── llm/
│   └── client.py           # OpenAI/Anthropic client with fallback
├── tests/                  # pytest + asyncio tests
├── config/                 # docker-compose.yml, .env.example
├── requirements.txt
└── Makefile
```

---

## Dev Commands

```bash
# Start local infra
cd sdv-mod-generator/config && docker compose up -d

# Install dependencies
pip install -r requirements.txt

# Run tests
make test              # Full test suite
make test-quick       # Skip integration tests

# Lint
make lint             # mypy + ruff

# Run API
cd sdv-mod-generator && PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# Test generation (blocking)
curl -X POST http://localhost:8000/v1/mods/generate \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","prompt":"做一个电视购物频道"}'

# Poll status
curl http://localhost:8000/v1/mods/status/{request_id}
```

---

## Key Constraints

- Python 3.11+ required
- All code needs type annotations (`mypy` enforced)
- Use `structlog` with snake_case field names
- All secrets via env vars — no hardcoding
- `max_t2_iterations` must be 0-2 (infinite loop prevention)
- Background tasks must use thread pool for I/O-heavy work

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/v1/mods/generate` | POST | Start generation (non-blocking) |
| `/v1/mods/status/{id}` | GET | Poll status from Redis |
| `/v1/mods/download/{id}` | GET | Presigned S3 download URL |
| `/v1/mods/{id}` | GET | Full status from Redis or DB |
| `/v1/mods/{id}/files` | GET | Generated file preview |
| `/v1/users/{id}/history` | GET | User history (API key required) |
| `/webhooks/discord` | POST | Discord interaction webhook |

---

## Conventions

- **File docstrings**: English, short
- **Logging**: structlog, not print
- **Quality gates**: T1 = deterministic schema check, T2 = 3-judge LLM panel
- **Mod output format**: Content Patcher zip (manifest.json + content.json + Assets/ + i18n/)
- **Background tasks**: Use `asyncio.to_thread()` or thread pool for blocking I/O
- **Configuration**: Validate dangerous configs (retries, timeouts) at startup

---

## Root Cause Patterns (from git history)

| Issue | Root Cause | Fix |
|-------|------------|-----|
| Infinite retry loop | `max_t2_iterations=2` + invalid LLM output = infinite loop | Set to 0 until retry logic proven |
| Background task never runs | `asyncio.create_task()` in uvicorn event loop doesn't schedule properly | Use thread pool for background I/O |
| T1 inverted logic | Contract not defined upfront | Define pass/fail contracts before implementation |
| Path traversal vuln | No input validation on file paths | Validate all user inputs, no `file://` URLs |
| Swallowed errors | Bare `except:` handlers | Catch specific exceptions, log and propagate |

---

## Automation Opportunities

1. **Worktree lifecycle**: Create → merge → delete as single atomic operation
2. **Stale worktree detection**: Daily cron removes merged worktrees
3. **Pipeline timeout test**: Every PR runs `_run_pipeline()` with 60s timeout
4. **Configuration sanity check**: Validate `max_t2_iterations < 3`, `timeout > 0` at startup
5. **Pre-commit worktree check**: Reject commit if stale worktrees exist
