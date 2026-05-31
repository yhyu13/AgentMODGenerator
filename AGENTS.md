# AGENTS.md — SDV Mod Generator

## Project Status

**Greenfield planning repo** — no source code exists yet. Only `README.md` (architecture, API, setup) and `PHASES.md` (build roadmap) are present.

---

## What This Project Does

AI generates complete Stardew Valley Content Patcher mods from a single user prompt (e.g., "make a TV shopping channel"). User sends request via Discord or API → AI generates zip → delivered back to user.

## Architecture

```
FastAPI → LangGraph orchestrator (Route → Generate → L1 Gate → L2 Gate → Package)
         ↓
       S3 (zip storage) + PostgreSQL (state) + Redis (cache)
```

**Entry point** (planned): `app/main.py` — endpoints `/health` and `/v1/mods/generate`

## Build Phases

| Phase | Goal |
|-------|------|
| P0 | End-to-end跑通，zip out |
| P1 | API layer + status polling |
| P2 | Discord bot |
| P3 | More generators (texture, NPC, events) |
| P4 | Testing + quality gates |
| P5 | Deploy + monitoring |

**Current priority**: P0.2 (storage layer), P0.3 (generators), P0.4 (knowledge base)

## Project Layout (Planned)

```
sdv-mod-generator/
├── app/               # FastAPI (main.py, discord/, config.py)
├── orchestrator/     # router.py, pipeline.py, state.py, nodes/
├── generators/        # base.py, registry.py, p0_*.py, p1_*.py, templates/
├── knowledge/         # cases/, data/ (item_ids.json, game_systems.json, content_actions.json)
├── quality/          # gate_l1.py, gate_l2.py
├── storage/          # postgres.py, redis.py, s3.py, models/
├── db/              # init.sql, migrations/
├── scripts/         # init_db.py, seed_knowledge.py
├── config/          # docker-compose.yml, .env.example
├── tests/           # test_generators.py, test_quality_gate.py, fixtures/
├── requirements.txt
└── Dockerfile
```

## Dev Commands (Planned)

```bash
# Dependencies
pip install -r requirements.txt

# Start local infra
cd config && docker compose up -d

# Init DB
python scripts/init_db.py

# Seed knowledge base
python scripts/seed_knowledge.py

# Run API
uvicorn app.main:app --reload --port 8000

# Test generation
curl -X POST http://localhost:8000/v1/mods/generate \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","prompt":"做一个电视购物频道"}'

# Pre-commit checks (when code exists)
mypy .           # type checking
ruff check .     # linting
pytest tests/    # tests
```

## Generator Development

1. Subclass `BaseGenerator` in `generators/`, implement `generate()` + `validate_output()`
2. Register in `generators/registry.py`
3. Add routing keywords in `orchestrator/router.py` (`FEATURE_TO_GENERATORS`, `FEATURE_TO_PHASE`)

## Key Constraints

- Python 3.11+ required
- All code needs type annotations (`mypy` enforced)
- Use `structlog` with snake_case field names
- All secrets via env vars — no hardcoding
- See `README.md` for env var list (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `DISCORD_BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, etc.)
- See `PHASES.md` for full task breakdown and dependencies

## Conventions

- **File docstrings**: English, short
- **Logging**: structlog, not print
- **Quality gates**: L1 = deterministic schema check, L2 = LLM judge
- **Mod output format**: Content Patcher zip (manifest.json + content.json + Assets/ + i18n/)
