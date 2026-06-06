# SDV Mod Generator — Onboarding Guide

Welcome to the SDV Mod Generator project! This guide provides a customized onboarding experience based on your role.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Role Selection](#role-selection)
3. [Track A: Backend Engineer (Pipeline, API, Storage)](#track-a-backend-engineer-pipeline-api-storage)
4. [Track B: Generator Developer (Mod Logic)](#track-b-generator-developer-mod-logic)
5. [Track C: Frontend/Discord Developer](#track-c-frontenddiscord-developer)
6. [Track D: ML/AI Engineer (Quality Gates, LLM Integration)](#track-d-mlai-engineer-quality-gates-llm-integration)
7. [Universal Day 1 Checklist](#universal-day-1-checklist)

---

## Prerequisites

### Required

- Python 3.11+
- Docker Desktop (for PostgreSQL + Redis)
- Git
- Code editor (VS Code recommended with Pylance extension)

### Recommended

- Basic understanding of async/await in Python
- Familiarity with FastAPI or similar web frameworks
- (Helpful but not required) Experience with Stardew Valley mods

---

## Role Selection

Choose your track based on your primary responsibility:

| Track | Role | Best For |
|-------|------|----------|
| **A** | Backend Engineer | Focus on pipeline orchestration, API endpoints, database/Redis integration |
| **B** | Generator Developer | Focus on mod generation logic, SDV game mechanics, Content Patcher |
| **C** | Frontend/Discord Dev | Focus on Discord bot, user interaction, API consumers |
| **D** | ML/AI Engineer | Focus on LLM integration, quality gates, prompt engineering |

You may overlap on multiple tracks — that's normal!

---

## Track A: Backend Engineer (Pipeline, API, Storage)

### Your Focus Areas

- `orchestrator/` — LangGraph pipeline (router.py, pipeline.py, state.py)
- `app/api/` — FastAPI routes and schemas
- `storage/` — PostgreSQL (asyncpg), Redis, S3

### Onboarding Tasks

#### Phase 1: Environment Setup (Day 1)

1. Clone the repo and set up virtual environment:
   ```bash
   cd sdv-mod-generator
   make install
   ```

2. Start local infrastructure:
   ```bash
   cd config && docker compose up -d
   ```

3. Verify connections:
   ```bash
   # Test PostgreSQL
   psql $DATABASE_URL -c "SELECT 1;"
   
   # Test Redis
   redis-cli ping
   ```

4. Initialize the database:
   ```bash
   python scripts/init_db.py
   ```

#### Phase 2: Understand the Pipeline (Day 1-2)

Read these files in order:

1. `orchestrator/state.py` — PipelineState data structures
2. `orchestrator/router.py` — How requests are routed to generators
3. `orchestrator/pipeline.py` — Main LangGraph workflow
4. `app/main.py` — FastAPI entry point
5. `app/api/routes.py` — API endpoint definitions

**Key concept**: The pipeline flows: `Route → Generate → L1 Gate → L2 Gate → Package`

#### Phase 3: Storage Layer Deep Dive (Day 2-3)

Study the storage implementation:

| File | Purpose |
|------|---------|
| `storage/postgres.py` | Async SQLAlchemy engine, session management |
| `storage/redis.py` | Pipeline state caching, rate limiting |
| `storage/s3.py` | Mod zip file storage |
| `storage/models/models.py` | ORM models (User, ModRequest, ModOutput) |

**Task**: Trace how a request flows through storage from API receipt to zip upload.

#### Phase 4: Make Your First Change (Day 3-4)

**Exercise**: Add a new field to the pipeline state and persist it through the pipeline.

1. Add a field to `PipelineState` in `orchestrator/state.py`
2. Update `storage/models/models.py` with a new column
3. Add a migration step (or update `scripts/init_db.py`)
4. Write the value somewhere in `orchestrator/pipeline.py`
5. Verify with `python scripts/init_db.py && make test`

### Key Files to Know

```
sdv-mod-generator/
├── orchestrator/
│   ├── state.py       # PipelineState dataclasses
│   ├── router.py      # Request → Generator routing
│   └── pipeline.py    # LangGraph workflow definition
├── app/
│   ├── main.py        # FastAPI app factory
│   ├── api/
│   │   ├── routes.py  # Endpoint definitions
│   │   └── schemas.py # Pydantic request/response models
│   └── config.py      # Environment variable loading
├── storage/
│   ├── postgres.py    # DB connection management
│   ├── redis.py       # Cache and state
│   ├── s3.py          # File storage
│   └── models/        # SQLAlchemy ORM models
└── db/
    └── init.sql       # Database schema
```

### Common Patterns

- **Async everywhere**: All DB/storage calls use `async def`
- **Structured logging**: Use `structlog`, not `print()`
- **Type annotations**: Required — run `mypy .` before committing

### Red Flags to Avoid

- Never hardcode secrets — always use env vars
- Don't block in async functions — use `await` or `asyncio`
- Never commit to main without running `make build`

---

## Track B: Generator Developer (Mod Logic)

### Your Focus Areas

- `generators/` — Mod generation classes
- `knowledge/` — SDV game data, Content Patcher references
- `orchestrator/router.py` — Feature routing

### Onboarding Tasks

#### Phase 1: Understand Stardew Valley Modding (Day 1-2)

1. Read `knowledge/data/content_actions.json` — Content Patcher action types
2. Read `knowledge/data/game_systems.json` — SDV game API summary
3. Read `knowledge/data/item_ids.json` — Item ID prefixes
4. Study `knowledge/cases/01-tv-shopping-network-case.md` — Full mod breakdown

**Key concept**: Content Patcher mods use `manifest.json` + `content.json` to patch game files.

#### Phase 2: Generator Architecture (Day 2-3)

Read these files:

1. `generators/base.py` — BaseGenerator, GeneratorInput, GeneratorOutput
2. `generators/registry.py` — Generator registration
3. `generators/packager.py` — How files get assembled into a zip

#### Phase 3: Study Existing Generators (Day 3-4)

Look at the existing implementations:

```bash
# Find generator files
ls generators/packs/stardew_valley/features/shop_channel/
ls generators/packs/stardew_valley/features/texture/
```

Pick one to study in detail — the shop_channel is fully implemented.

#### Phase 4: Create Your First Generator (Day 4-5)

**Exercise**: Create a simple generator that adds a custom mail letter.

1. Create `generators/packs/stardew_valley/features/mail/my_mail_generator.py`
2. Inherit from `BaseGenerator`
3. Implement `generate(inp: GeneratorInput) -> GeneratorOutput`
4. Implement `validate_output(output: GeneratorOutput) -> list[str]`
5. Register in `generators/registry.py`
6. Add routing keywords in `orchestrator/router.py`:
   ```python
   FEATURE_TO_GENERATORS = {
       "mail": ["gen_custom_mail"],
   }
   FEATURE_TO_PHASE = {
       "mail": "p1_shop_channel",
   }
   ```
7. Test: Send a prompt mentioning mail and verify it triggers your generator

### Key Files to Know

```
sdv-mod-generator/
├── generators/
│   ├── base.py              # BaseGenerator abstract class
│   ├── registry.py         # Generator registration
│   ├── packager.py          # ZIP assembly
│   ├── core/
│   │   ├── base.py         # Core generator abstractions
│   │   └── pack.py         # Pack generation utilities
│   └── packs/stardew_valley/
│       └── features/
│           ├── shop_channel/  # Complete reference implementation
│           └── texture/      # Texture replacement example
├── knowledge/
│   ├── data/
│   │   ├── item_ids.json         # Item ID prefixes
│   │   ├── game_systems.json    # SDV API summary
│   │   └── content_actions.json  # CP action types
│   └── cases/
│       └── 01-tv-shopping-network-case.md
└── orchestrator/
    └── router.py            # Feature → Generator routing
```

### Generator Development Checklist

- [ ] Subclass `BaseGenerator`
- [ ] Implement `generate()` returning `GeneratorOutput`
- [ ] Implement `validate_output()` returning error list
- [ ] Register in `registry.py`
- [ ] Add keywords to `router.py` `FEATURE_TO_GENERATORS`
- [ ] Add phase to `router.py` `FEATURE_TO_PHASE`
- [ ] Write test in `tests/test_generators.py`
- [ ] Verify with `make lint && mypy .`

### Common Patterns

- Files dict: `output.files["content.json"] = {...}`
- Assets: `output.add_asset("/path/to/sprite.png")`
- Metadata: `output.metadata["shop_items"] = [...]`
- Always validate: Check required files exist in `validate_output()`

---

## Track C: Frontend/Discord Developer

### Your Focus Areas

- `app/discord/` — Discord bot implementation
- `app/api/routes.py` — API endpoints for frontend consumption
- User interaction design

### Onboarding Tasks

#### Phase 1: Discord Bot Setup (Day 1)

1. Create a Discord bot in the Developer Portal
2. Add bot token to `config/.env` as `DISCORD_BOT_TOKEN`
3. Run the bot:
   ```bash
   python -m app.discord.bot
   ```
4. Verify it connects — you should see "Logged in as [Bot Name]" in logs

#### Phase 2: Understand Bot Architecture (Day 1-2)

Read these files:

1. `app/discord/bot.py` — Main bot setup, event handlers
2. `app/discord/commands.py` — Slash command definitions
3. `app/discord/connector.py` — WebSocket connection management
4. `app/discord/webhook.py` — Status update webhooks

#### Phase 3: Study the API Layer (Day 2-3)

Read these files:

1. `app/api/routes.py` — What endpoints exist
2. `app/api/schemas.py` — Request/response shapes
3. `app/main.py` — How routes are registered

**Task**: Trace how a user request flows from Discord → API → pipeline → back to Discord.

#### Phase 4: Add a New Slash Command (Day 3-4)

**Exercise**: Add a `/mod preview <request_id>` command.

1. Add command definition in `app/discord/commands.py`
2. Implement the handler that calls `GET /v1/mods/{request_id}/files`
3. Format the response nicely for Discord (embeds, etc.)
4. Test with a known request_id

### Key Files to Know

```
sdv-mod-generator/
├── app/
│   ├── main.py           # FastAPI app
│   ├── api/
│   │   ├── routes.py     # All endpoints
│   │   └── schemas.py    # Pydantic models
│   └── discord/
│       ├── bot.py        # Bot entry point
│       ├── commands.py    # Slash command definitions
│       ├── connector.py  # Connection management
│       └── webhook.py    # Status push webhooks
```

### Discord Bot Commands Reference

| Command | Description |
|---------|-------------|
| `/mod generate <description>` | Start mod generation |
| `/mod status <request_id>` | Check generation status |
| `/mod history` | View past generations |

### Common Patterns

- Use `discord.Embed` for rich messages
- Always handle errors gracefully with `try/except` and user-friendly messages
- Rate limit commands to prevent abuse
- Use buttons for interactive elements (re-generate, download, etc.)

---

## Track D: ML/AI Engineer (Quality Gates, LLM Integration)

### Your Focus Areas

- `quality/` — L1 (schema) and L2 (LLM judge) quality gates
- `llm/` — LLM client abstraction
- `orchestrator/` — Pipeline flow, prompt construction

### Onboarding Tasks

#### Phase 1: Understand the Quality Gates (Day 1-2)

Read these files:

1. `quality/gate_t1.py` — L1 deterministic schema validation
2. `quality/gate_t2.py` — L2 LLM-based quality judgment
3. `llm/client.py` — LLM abstraction layer

**Key concept**:
- **L1 Gate**: Fast, deterministic JSON Schema validation of manifest.json and content.json
- **L2 Gate**: Slower, uses LLM to judge whether the generated mod makes sense

#### Phase 2: Study Prompt Engineering (Day 2-3)

Look at how prompts are constructed:

1. `generators/llm_utils.py` — Common LLM utilities
2. `generators/packs/stardew_valley/features/shop_channel/__init__.py` — How a generator uses LLM

**Task**: Trace how a generator decides what to prompt the LLM.

#### Phase 3: Understand the Pipeline Integration (Day 3-4)

Read `orchestrator/pipeline.py` to see where gates are called:
- L1 is called after all generators complete
- L2 is called after L1 passes
- Gates can reject and trigger retry or fallback

#### Phase 4: Improve the L2 Gate (Day 4-5)

**Exercise**: Add a new evaluation criterion to L2 gate.

1. Edit `quality/gate_t2.py`
2. Add a new check (e.g., "Does the mod have i18n support?")
3. Update the prompt to include this criterion
4. Add test cases in `tests/test_quality_gate.py`
5. Verify with `make test`

### Key Files to Know

```
sdv-mod-generator/
├── quality/
│   ├── gate_t1.py      # L1: JSON Schema validation
│   └── gate_t2.py      # L2: LLM judge
├── llm/
│   └── client.py       # LLM API abstraction
├── generators/
│   ├── llm_utils.py   # Prompt construction utilities
│   └── packs/stardew_valley/features/
│       └── shop_channel/  # Reference generator with LLM usage
└── orchestrator/
    └── pipeline.py     # Where gates are invoked
```

### L2 Gate Evaluation Criteria (Current)

- Mod completeness (all promised features present)
- Content Patcher format correctness
- i18n consistency (English keys match values)
- Game logic coherence
- File structure validity

### Common Patterns

- L1 must be fast — no LLM calls, pure schema validation
- L2 prompt includes: generated content + evaluation criteria + score output format
- Score thresholds defined in `quality/gate_t2.py` (configurable)
- Fallback: If L2 fails with retries exhausted, mark as "needs_human_review"

### Prompt Engineering Tips

- Be specific about output format (JSON with fields: score, reasons, suggested_fixes)
- Include examples of good and bad outputs in the prompt
- Keep prompts under 8k tokens for cost efficiency
- Use function calling if available for structured outputs

---

## Universal Day 1 Checklist

Regardless of your track, complete these on your first day:

### Before Lunch

- [ ] Clone the repository
- [ ] Run `make install` to set up Python environment
- [ ] Start Docker services: `cd config && docker compose up -d`
- [ ] Copy `config/.env.example` to `config/.env` and fill in required keys
- [ ] Run `python scripts/init_db.py` to initialize the database
- [ ] Verify `curl http://localhost:8000/health` returns `{"status": "ok"}`

### After Lunch

- [ ] Read `README.md` completely
- [ ] Read `AGENTS.md` for project conventions
- [ ] Read `PHASES.md` to understand the roadmap
- [ ] Run the test suite: `make test` (should mostly pass)
- [ ] Run the linter: `make lint` (should have no errors)
- [ ] Make a small harmless change (e.g., add a comment) and verify CI passes

### By End of Week 1

- [ ] Complete your track-specific Phase 1-2 tasks
- [ ] Make one meaningful contribution to your track
- [ ] Get your code reviewed and merged
- [ ] Understand how to run the full pipeline end-to-end

---

## Vibe Coding with Kilo — Multi-Session Code Critic Workflow

This project embraces "vibe coding" — an iterative, AI-assisted development approach where you steer the AI with high-level intent while it handles implementation. The key to quality output is the **multi-session code critic review pattern**: you run two parallel Kilo sessions, one implementing and one critically reviewing.

### The Core Pattern: Implement + Skeptic

```
┌─────────────────────────────────────────────────────────────┐
│  Session A: CODE AGENT                                     │
│  - Receives tasks from you                                 │
│  - Implements features, writes tests, runs linters         │
│  - Reports: "I did X, Y, Z"                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
                            ↓ shares workspace
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Session B: CODE SKEPTIC AGENT                             │
│  - Watches Session A's claims                              │
│  - Demands proof: "Show me the logs"                       │
│  - Catches shortcuts, skipped steps, incomplete work       │
│  - Reports: "Session A claimed X but didn't verify Y"      │
└─────────────────────────────────────────────────────────────┘
                            ↓
                            ↓ you synthesize feedback
                            ↓
                         [ Iteration ]
```

### How to Set Up Sessions

#### Session 1: Code Agent (Implementation)

Start a Kilo session in VS Code or CLI targeting this project:

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator
kilo --agent code --project .
```

Or use VS Code Kilo extension with the **code** agent selected.

#### Session 2: Code Skeptic Agent (Critical Review)

Start a second Kilo session (can be in a split terminal or separate window):

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator
kilo --agent code-skeptic --project .
```

The Code Skeptic agent is pre-configured in your `~/.config/kilo/agents/code-skeptic.md`. It will:
- Challenge every claim of success
- Demand to see actual command output, not assumptions
- Flag skipped steps and shortcuts
- Enforce project conventions

### The Workflow

#### Step 1: Architect a Plan (Optional but Recommended)

For larger features, use the **Architect** agent first:

```bash
kilo --agent architect --project .
```

Describe what you want to build. The Architect will ask clarifying questions, challenge vague requirements, and produce an implementation-ready plan in `.kilo/plans/`.

Then switch to implementation.

#### Step 2: Implement Incrementally

Give the Code Agent small, concrete tasks. The Skeptic watches.

**Good prompt:**
> "Add a `created_at` field to the `ModRequest` model in `storage/models/models.py`. Update the init SQL to include the column. Run `make lint && mypy .` to verify."

**Bad prompt:**
> "Build the entire storage layer." (too large for effective review)

#### Step 3: Skeptic Reviews

The Code Skeptic will notice things like:
- "The agent claimed `make test` passed but didn't show output"
- "The agent skipped updating `FEATURE_TO_GENERATORS` in `router.py`"
- "The agent used `print()` instead of `structlog`"
- "Type annotations missing in `generators/base.py`"

#### Step 4: Synthesize and Iterate

You receive both agents' outputs. You decide:
- Is the work good enough to move on?
- Should the Code Agent fix the issues the Skeptic caught?
- Is the plan correct, or do we need to re-architect?

#### Step 5: Commit (Only After Skeptic Approval)

The Skeptic should verify:
- [ ] All tests pass (`make test`)
- [ ] Lint passes (`make lint`)
- [ ] Type check passes (`mypy .`)
- [ ] No skipped steps from the plan
- [ ] No shortcuts or workarounds
- [ ] Proper error handling exists

### Session Prompt Examples

#### Starting a Feature (Code Agent)

```markdown
I want to add a new generator for custom mail letters.

Read these files first:
- generators/base.py
- generators/registry.py
- knowledge/data/content_actions.json (look for mail-related actions)

Follow the Generator Development Checklist in docs/ONBOARDING.md.

When done, show me:
1. Your implementation of `generate()` and `validate_output()`
2. The registration in registry.py
3. The routing keywords added to router.py
4. Output of `make lint && mypy .`
```

#### Review Request (Code Skeptic)

```markdown
A Code Agent just finished implementing a new mail generator. I need you to review their work.

Look at:
- generators/packs/stardew_valley/features/mail/
- generators/registry.py (look for mail-related entries)
- orchestrator/router.py (look for mail in FEATURE_TO_GENERATORS)

Verify:
1. Did they actually implement `generate()` and `validate_output()`?
2. Did they register the generator properly?
3. Did they add routing keywords?
4. Run `make lint && mypy .` and show me the output.
5. Check if they used `structlog` instead of `print()`.

Report any gaps. Be skeptical — don't accept "it works" without proof.
```

### When to Use Each Agent

| Situation | Agent | Notes |
|-----------|-------|-------|
| Implementing a feature | Code | Small increments preferred |
| Catching bugs before they happen | Code Skeptic | Always running in parallel recommended |
| Planning a new component | Architect | Before writing any code |
| Debugging a failing test | Code + Skeptic | Code investigates, Skeptic verifies fix |
| Reviewing a PR or large change | Code Skeptic | Request focused review on specific files |
| Understanding existing code | Explore (Task tool) | Not a persistent session |

### Common Skeptic Triggers

The Code Skeptic will flag these automatically, but you can also watch for them:

| Red Flag | Why It Matters |
|----------|----------------|
| "It should work" | Means they didn't run it |
| No test output shown | Tests may not actually pass |
| Skipped `make lint` | Convention violations will accumulate |
| In-memory workaround | Will cause bugs in production |
| No type annotations | mypy will fail |
| Comments in Chinese | Project requires English |

### Multi-Session Tips

1. **Keep sessions in sync**: Pull latest (`git pull`) before each session start
2. **Share file paths explicitly**: "Look at `app/main.py:42`" rather than "the main file"
3. **One session writes, one watches**: Don't have both agents editing the same file simultaneously
4. **Take notes**: The Code Agent's output is the source of truth for what was changed
5. **Escalate to Architect**: If the Skeptic finds fundamental design issues, re-plan first

### Keyboard Shortcuts (VS Code Extension)

| Action | Shortcut |
|--------|----------|
| Start new Code session | `Cmd/Ctrl + Shift + K` then select "code" |
| Start new Code Skeptic session | `Cmd/Ctrl + Shift + K` then select "code-skeptic" |
| Switch between sessions | `Cmd/Ctrl + Tab` |
| Pull latest changes | `Cmd/Ctrl + Shift + P` → "Kilo: Pull" |

### Agent Manager (Parallel Sessions)

For complex features, use **Agent Manager** to create parallel worktrees:

```bash
# From VS Code Command Palette
# "Agent Manager: New Worktree"
```

Each worktree can run a different agent on a different branch. This is useful when:
- Implementing multiple features in parallel
- One feature is waiting for code review
- You want to isolate experimental changes

---

## Additional Resources

### External Links

- [Content Patcher Documentation](https://github.com/Pathoschild/StardewValley.Mods/blob/master/documents/content-patcher.md)
- [SMAPI Mod Documentation](https://stardewvalleywiki.com/Modding:Index)
- [Discord.py Documentation](https://discordpy.readthedocs.io/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)

### Internal Documentation

| Document | Purpose |
|----------|---------|
| `README.md` | Project overview, architecture, quick start |
| `PHASES.md` | Detailed build roadmap with task breakdown |
| `CLAUDE.md` | Claude Code specific guidance |
| `knowledge/cases/01-tv-shopping-network-case.md` | Full mod analysis example |

### Getting Help

1. Check existing issues on GitHub
2. Ask in the project Discord channel
3. Tag a senior developer for review

---

## Quick Reference Commands

```bash
# Setup
make install          # Install dependencies
make run             # Start API server
make test            # Run tests
make lint            # Type check + lint
make build           # Full verification (test + lint)
make clean           # Clean cache files

# Infrastructure
cd config && docker compose up -d    # Start PostgreSQL + Redis
docker compose down                   # Stop infrastructure

# Database
python scripts/init_db.py            # Initialize DB
python scripts/seed_knowledge.py      # Seed knowledge base

# Development
uvicorn app.main:app --reload --port 8000  # Run API with hot reload
python -m app.discord.bot                 # Run Discord bot

# Testing
pytest tests/ -v                       # Run all tests
pytest tests/test_generators.py -v     # Run generator tests
mypy sdv-mod-generator/                # Type check
ruff check .                           # Lint
```

---

*Last updated: 2026-06-03*
