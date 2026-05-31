# Plan: Architecture Review + Stub/Impl Phase Restructure

## Context

Current architecture is reasonable but has risks:
1. LLM generation quality is the hardest unsolved problem — not explicitly planned for iteration
2. No clear separation between "interface/contract" and "implementation"
3. Phase dependencies are implicit, not enforced by structure
4. L2 Gate (LLM-as-judge) has a self-referential reliability problem

Restructuring each phase into **stub** (contract + minimal code) and **impl** (real logic) makes the project future-proof:
- Stub phase: define interfaces, data shapes, and what "success" looks like
- Impl phase: implement real logic, test against stub contracts
- Enables parallel workstreams once interfaces stabilize

**Risk-derived design decisions (incorporated):**
- L2 split into Tier 1 (deterministic) + Tier 2 (LLM advisory) — eliminates circular self-judging
- P0-impl split into P0a (storage + pipeline) and P0b (generators) — reduces single-phase scope
- Dependencies flattened: P*-stub starts after P*(n-1)-stub completes (not P*-impl) — enables parallelism
- Explicit stub review gate before each impl — prevents interface drift

---

## Changes

### 1. README.md — Add Architecture Principles + Refactor Pipeline

Add a section "Architecture Principles" and refactor the pipeline description to be more explicit about failure modes and contracts.

### 2. PHASES.md — Restructure All Phases into Stub/Impl

Each P* becomes two sub-phases:
- **P*-stub**: Interface definition, type stubs, minimal working code (compile + run as mock)
- **P*-impl**: Real implementation against the stub contracts

---

## Detailed New Phase Structure

### Phase 0 — End-to-End (Stub + Impl)

#### P0-stub — Define Pipeline Contract
Goal: All interfaces exist, pipeline can run with mock data end-to-end

Tasks:
- [ ] `orchestrator/state.py` — `PipelineState` dataclass with all fields typed
- [ ] `orchestrator/pipeline.py` — LangGraph graph definition (stubs only, nodes call pass-through)
- [ ] `orchestrator/router.py` — `FEATURE_TO_GENERATORS` / `FEATURE_TO_PHASE` dicts exist (keyword matching stub)
- [ ] `generators/base.py` — `BaseGenerator`, `GeneratorInput`, `GeneratorOutput` abstract + typed
- [ ] `generators/registry.py` — empty registry with type hints
- [ ] `generators/p0_texture.py` — stub class that returns `GeneratorOutput` with empty files
- [ ] `generators/p1_shop_channel.py` — stub classes for all generators in table
- [ ] `storage/postgres.py` — abstract interface (actual connection optional)
- [ ] `storage/redis.py` — abstract interface (actual connection optional)
- [ ] `storage/s3.py` — abstract interface (actual upload optional)
- [ ] `storage/models/` — SQLAlchemy model stubs (tables defined in `db/init.sql`)
- [ ] `quality/gate_t1.py` — stub that always passes (Tier 1 deterministic)
- [ ] `quality/gate_t2.py` — stub that always passes with score=10 (Tier 2 LLM advisory)
- [ ] `generators/packager.py` — stub that writes a minimal valid zip (no real content)
- [ ] `app/main.py` — FastAPI app with `/health` (returns ok) and `/v1/mods/generate` (returns mock request_id, logs "stub")
- [ ] `db/init.sql` — complete DDL for all tables

**Completion criteria**: `uvicorn app.main:app` starts; `curl POST /v1/mods/generate` returns `request_id`; pipeline logs show Route → Generate → L1 → L2 → Package all executed (stub mode).

#### P0a-impl — Storage + Pipeline Implementation
Goal: Storage layer works, pipeline executes end-to-end with mock generators

Tasks:
- [ ] `storage/postgres.py` — real async SQLAlchemy connection + session management
- [ ] `storage/redis.py` — real Redis connection + pipeline state read/write
- [ ] `storage/s3.py` — real boto3 upload/download
- [ ] `storage/models/` — SQLAlchemy ORM models (User, ModRequest, ModOutput, ModHistory)
- [ ] `orchestrator/router.py` — real keyword matching + hint generation
- [ ] `quality/gate_t1.py` — Tier 1 deterministic checks (see Quality Gate Design below)
- [ ] `quality/gate_t2.py` — Tier 2 LLM judge (advisory, never blocks)
- [ ] `generators/packager.py` — real zip creation + S3 upload
- [ ] `scripts/init_db.py` — database initialization script

**Completion criteria**: Pipeline executes Route → Generate → T1 → T2 → Package with mock generators; storage reads/writes work; zip is created in S3 (even if content is stub).

**Review gate for P0b-impl (must pass before generators work starts):**
- [ ] `GeneratorOutput` shape is frozen (no changes to field names or types in subsequent work)
- [ ] `GeneratorOutput.validate_output()` signature is stable — all generators implement it with the same return type `list[str]`
- [ ] Storage layer stores `GeneratorOutput` correctly (verify with a test write/read round-trip)
- [ ] All generator stubs can import and use `GeneratorOutput` without modification

#### P0b-impl — Generator Implementation
Goal: All P1 shop channel generators produce valid Content Patcher content

**Dependency**: Starts only after P0a-impl review gate passes — specifically after `GeneratorOutput` shape is confirmed stable.

Tasks:
- [ ] `generators/p1_shop_channel.py` — implement all generator classes:
  - `ManifestGenerator` — manifest.json
  - `ShopItemPoolGenerator` — Data/ShopTeleportLocations.json
  - `TVChannelGenerator` — TV/mail/character schedule entries
  - `MailSystemGenerator` — mail definitions
  - `ItemSpritesGenerator` — sprite asset references
  - `UIAssetsGenerator` — UI image assets
  - `CatalogPreviewGenerator` — preview data
  - `RealismDamageGenerator` — damage multiplier config
  - `TriggerLogicGenerator` — special action triggers
  - `ConfigSchemaGenerator` — config.json schema
- [ ] `generators/p0_texture.py` — implement texture replacement generator
- [ ] `knowledge/data/` — fill in item_ids.json, game_systems.json, content_actions.json

**Completion criteria**: `curl POST /v1/mods/generate` with "做一个电视购物频道" produces a zip containing valid manifest.json + content.json + i18n/default.json.

---

### Phase 1 — API + Polling (Stub + Impl)

#### P1-stub — Define API Contracts
Goal: All API endpoints defined with request/response schemas, real impl not required

Tasks:
- [ ] `app/api/routes.py` — define all route handlers with Pydantic request/response models, stub implementations
- [ ] `app/api/schemas.py` — all request/response types (GenerateRequest, GenerateResponse, StatusResponse, HistoryResponse, ErrorResponse)
- [ ] `GET /v1/mods/{request_id}` — stub returns mock status
- [ ] `GET /v1/mods/{request_id}/files` — stub returns mock file list
- [ ] `GET /v1/users/me/history` — stub returns mock history
- [ ] Error responses: 404, 500, 503 defined with schema

**Completion criteria**: OpenAPI docs (Swagger UI at `/docs`) show all endpoints with correct schemas.

#### P1-impl — Real API Implementation
Tasks:
- [ ] Real route implementations reading from PostgreSQL + Redis
- [ ] Redis cache-first lookup, PostgreSQL fallback for `/v1/mods/{request_id}`
- [ ] User history from `mod_history` table
- [ ] Proper error handling with correct HTTP status codes

---

### Phase 2 — Discord Bot (Stub + Impl)

#### P2-stub — Define Bot Interface
Tasks:
- [ ] `app/discord/bot.py` — discord.py bot skeleton, responds to `on_message` (stub)
- [ ] `app/discord/connector.py` — WebSocket connector stub (just logs)
- [ ] Slash commands defined: `/mod generate`, `/mod status`, `/mod history` (stub handlers)
- [ ] `app/discord/webhook.py` — webhook receiver stub

**Completion criteria**: Bot connects to Discord (with fake token), commands appear in Discord UI but do nothing.

#### P2-impl — Real Bot Implementation
Tasks:
- [ ] Real `/mod generate` — calls API, returns request_id
- [ ] Real `/mod status` — polls API, returns status
- [ ] Real `/mod history` — shows user's recent mods
- [ ] `connector.py` — real WebSocket/long-polling for status push
- [ ] Status change detection (Redis pubsub or polling) → Discord message update
- [ ] Image attachment handling (upload to OSS, pass URL to generator)

---

### Phase 3 — Generator Coverage (Stub + Impl)

#### P3-stub — Define Generator Interfaces for New Types
Tasks:
- [ ] `generators/p1_npc.py` — stub class (returns empty output)
- [ ] `generators/p1_npc_dialogue.py` — stub
- [ ] `generators/p1_npc_schedule.py` — stub
- [ ] `generators/p1_npc_sprite.py` — stub
- [ ] `generators/p1_event.py` — stub
- [ ] `generators/p1_trigger.py` — stub
- [ ] Update `generators/registry.py` with all new stubs
- [ ] Update `orchestrator/router.py` with routing keywords for new generators

#### P3-impl — Implement New Generators
Tasks:
- [ ] Implement each NPC generator
- [ ] Implement event/trigger generators
- [ ] P3.4 — Router upgrade to LLM-based routing

---

### Phase 4 — Quality + Testing (Stub + Impl)

#### P4-stub — Define Test Interfaces
Tasks:
- [ ] `tests/conftest.py` — pytest fixtures for mocks (mock LLM, mock storage)
- [ ] `tests/test_generators.py` — test stubs (just `pass`), one per generator
- [ ] `tests/test_router.py` — test stubs for routing
- [ ] `tests/test_quality_gate.py` — test stubs for L1/L2
- [ ] `tests/test_pipeline_integration.py` — integration test stub (just `pass`)
- [ ] `tests/fixtures/` — test prompt samples (normal, edge case, error)

**Completion criteria**: `pytest tests/ -v` runs but all tests are skipped (stub).

#### P4-impl — Real Tests + Quality
Tasks:
- [ ] Implement all generator unit tests (mock LLM, fixed seeds)
- [ ] Implement router keyword tests
- [ ] Implement L1/L2 gate tests with known error samples
- [ ] Implement full pipeline integration test (hits real API, checks zip output)
- [ ] L2 prompt tuning with 20 labeled samples
- [ ] Coverage > 70%

---

### Phase 5 — DevOps (Stub + Impl)

#### P5-stub — Define Deployment Contracts
Tasks:
- [ ] `Dockerfile` — multi-stage build (stub, just COPY files)
- [ ] `docker-compose.prod.yml` — production config stub
- [ ] `.github/workflows/ci.yml` — CI workflow stub (runs `mypy .` and `ruff check .` only)
- [ ] Sentry integration stub (just `import sentry_sdk; sentry_sdk.init()`)

**Completion criteria**: `docker build .` succeeds, produces image that starts but returns 500 (no real app).

#### P5-impl — Real Deployment
Tasks:
- [ ] Real Dockerfile with proper dependency installation
- [ ] Production docker-compose with proper env vars, restart policies, health checks
- [ ] GitHub Actions CI/CD: test → build → deploy
- [ ] Real Sentry error tracking + performance monitoring
- [ ] Metrics: request success rate, L1/L2 pass rate, generation latency, concurrency

---

## Dependency Chain (Updated)

```
P0-stub → P0a-impl ──────────────────────────→ P0b-impl
   │                                                    │
   ↓                                                    ↓
P1-stub ───────────────────→ P1-impl
   │                                                    │
   ↓                                                    ↓
P2-stub ───────────────────→ P2-impl
   │                                                    │
   ├────────────────────┐                              │
   ↓                    ↓                              ↓
P3-stub ────────→ P3-impl
   │                  │
   ├────────────────┐  │
   ↓                ↓  ↓
P4-stub ────→ P4-impl
   │           │
   ├─────────┐  │
   ↓         ↓  ↓
P5-stub → P5-impl
```

**Key principles:**
- Never start P*-impl until P*-stub is accepted via review gate
- P1-stub can start as soon as P0-stub is accepted (not waiting for P0a-impl)
- P0a-impl and P1-stub can run in parallel after P0-stub accepted
- P3+ stubs wait for their respective P(n-1)-impl to complete

**Review gate checklist before each impl:**
- [ ] All interfaces defined and documented
- [ ] Type stubs compile without errors
- [ ] Stub implementations return valid (mock) data shapes
- [ ] Completion criteria reviewed and agreed
- [ ] Open questions resolved or deferred with explicit ticket

---

## Key Architecture Decisions to Document

### 1. LLM Provider Abstraction
Both OpenAI and Anthropic supported via env vars. Provider selected at startup via `LLM_CLIENT` env var. Abstraction layer in `llm/client.py` with a `CompletionClient` protocol.

### 2. Generator Contract
Every generator must:
- Subclass `BaseGenerator`
- Implement `generate(inp: GeneratorInput) -> GeneratorOutput`
- Implement `validate_output(output: GeneratorOutput) -> list[str]`
- Declare its `phase: str` and `name: str`
- Register in `generators/registry.py`

### 3. Pipeline State Contract
`PipelineState` is the single source of truth passed through all LangGraph nodes:
```python
@dataclass
class PipelineState:
    request_id: str
    user_id: str
    prompt: str
    phase: str
    generators: list[str]
    hint: dict
    outputs: dict[str, GeneratorOutput]  # keyed by generator name
    errors: list[str]
    zip_key: str | None
    status: Literal["pending", "routing", "generating", "gating", "packaging", "done", "failed"]
```

### 4. Quality Gate Design (Tier 1 + Tier 2)

The quality gate is split into two tiers to eliminate the circular self-judging problem:

**Tier 1 — Deterministic Checks (always run, must pass to proceed)**
- Valid JSON (manifest.json, content.json)
- Required fields present (manifest.json: Name, Author, Version, Content)
- Field values in valid range (price ≥ 0, sprite IDs exist in item_ids.json, Version matches semver)
- Schema compliance for Content Patcher actions (EditImage, EditData, EditMap)
- No broken internal references (Target → FromFile exists)
- i18n keys referenced in content.json exist in i18n files

**Tier 2 — LLM Semantic Judge (advisory only, never blocks)**
- Does the mod make sense semantically?
- Do item descriptions match their prices?
- Do mail triggers reference valid events?
- Is the overall mod coherent and not obviously exploitative?
- Score 1-10, log feedback, but never fail the pipeline on T2 alone

**Tier 1 catches ~60-70% of obvious failures deterministically**, reducing LLM judge's burden.

**Failure mode mapping:**
- **Generator failure** → template fallback + retry (max 2 retries per generator)
- **Tier 1 failure** → template fallback + notify user of specific error
- **Tier 2 low score** → flag for human review, proceed with generation (advisory only)
- **Storage failure** → retry 3x with exponential backoff, then fail with clear error
- **LLM rate limit** → switch to fallback provider, retry original after 60s

---

## Files to Modify

| File | Action |
|------|--------|
| `README.md` | Add Architecture Principles section, update env var table |
| `PHASES.md` | Replace all phases with stub/impl structure |

---

## Open Questions (resolved/deferred)

1. ~~L2 reliability~~ → **Resolved**: Split into Tier 1 (deterministic) + Tier 2 (LLM advisory). Tier 1 must pass; Tier 2 is advisory only.
2. **Fallback provider retry**: Should fallback happen automatically or require user opt-in? → Deferred to P0a-impl spike.

   **Spike definition**: 2-4 hour time-boxed investigation with a clear deliverable: `docs/adr/YYYY-MM-DD-llm-fallback-strategy.md` — an ADR recording:
   - What "fallback" means (same model/different provider, different model/same provider, or both)
   - Switching logic (automatic on 429? 500? After N retries?)
   - Whether fallback preserves original request or restarts
   - Decision rationale

3. **P3.4 LLM routing**: Same LLM as generation or cheaper model? → Deferred to P3.4 stub phase. **Criterion**: P3.4 must meet ≥95% routing accuracy on test set, regardless of model. Cheaper model is acceptable if it hits that bar. If it doesn't, fall back to the primary model used for generation.