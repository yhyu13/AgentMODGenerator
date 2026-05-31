# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SDV Mod Generator** — AI-powered system that generates Stardew Valley Content Patcher mods from natural language prompts. Users describe what they want via Discord/API, and receive a complete mod zip package.

## Tech Stack

- **Python 3.11+** with async/await throughout
- **FastAPI** — HTTP API layer
- **LangGraph** — pipeline orchestration (Route → Generate → L1 Gate → L2 Gate → Package)
- **PostgreSQL** (async via asyncpg) — persistent storage for requests/history
- **Redis** — pipeline state cache, rate limiting
- **S3/OSS** — mod zip file storage
- **Discord.py** — bot for user interaction

## Common Commands

### Development
```bash
# Install dependencies
pip install -r requirements.txt

# Start local services (PostgreSQL + Redis)
cd config && docker compose up -d

# Run API server with hot reload
uvicorn app.main:app --reload --port 8000

# Initialize database tables
python scripts/init_db.py

# Seed knowledge base data
python scripts/seed_knowledge.py
```

### Testing & Quality
```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_generators.py -v

# Type checking
mypy sdv-mod-generator/

# Linting/formatting
ruff check .
```

### Discord Bot
```bash
python -m app.discord.bot
```

## Architecture

### Pipeline Flow
```
User prompt → Router (keyword matching) → Generator(s) → L1 Gate (schema validation)
→ L2 Gate (LLM judge) → Packager (ZIP + S3) → Discord push / API response
```

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `app/` | FastAPI entry point (`main.py`), Discord bot connector |
| `orchestrator/` | LangGraph pipeline: `router.py`, `pipeline.py`, `state.py`, `nodes/` |
| `generators/` | Mod generator classes. Add new generator here, register in `registry.py` |
| `knowledge/` | Routing hints, SDV game data (`item_ids.json`, `game_systems.json`, `content_actions.json`), case studies |
| `quality/` | `gate_l1.py` (schema校验), `gate_l2.py` (LLM judge) |
| `storage/` | `postgres.py`, `redis.py`, `s3.py` + SQLAlchemy models |
| `db/` | `init.sql` for schema |
| `tests/` | `test_generators.py`, `test_quality_gate.py`, `test_pipeline.py` |
| `scripts/` | `init_db.py`, `seed_knowledge.py` |
| `config/` | `docker-compose.yml`, `.env.example` |

### Adding a New Generator

1. Create `generators/p1_your_feature.py` inheriting from `BaseGenerator`
2. Implement `generate(inp: GeneratorInput) -> GeneratorOutput` and `validate_output(output: GeneratorOutput) -> list[str]`
3. Register in `generators/registry.py`: `_GENERATOR_REGISTRY["gen_your_feature"] = YourFeatureGenerator`
4. Add keyword routing in `orchestrator/router.py` → `FEATURE_TO_GENERATORS` and `FEATURE_TO_PHASE`

### Knowledge Base

The `knowledge/` directory is critical for correct routing:
- `knowledge/data/item_ids.json` — SDV item/furniture ID prefixes
- `knowledge/data/game_systems.json` — SDV game API summary
- `knowledge/data/content_actions.json` — Content Patcher Action types
- `knowledge/cases/*.md` — analyzed mod source code breakdowns

## API Endpoints

- `POST /v1/mods/generate` — initiate mod generation
- `GET /v1/mods/{request_id}` — check status/result
- `GET /v1/mods/{request_id}/files` — preview generated files
- `GET /v1/users/me/history` — user's generation history

## Environment Variables

See `config/.env.example`. Required: `OPENAI_API_KEY`, `DISCORD_BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`. Optional: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET`.

## Project Phases

Current phase and pending work are documented in `PHASES.md`. Phase 0 (end-to-end manual run) is the immediate goal, followed by API layer, Discord bot integration, and expanded generator coverage.
