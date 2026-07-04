# P3-P5 Extraction Schedule (2026-07-03)

## What's left from discord-ops-hardening

After 5 PRs + 22 cron rounds (8 sessions of work), master has the
Discord security/UX layer, the router weather priority, the security
headers, and the feature_flag helper module. Remaining:

### A. 20 new API endpoints (in `app/api/routes.py`)

Master has 8 routes; branch has 36. The 20 new ones are P3-P5 features.
Grouped by purpose:

| Group | Endpoints | Lines (estimated, handler only) |
|-------|-----------|----------------|
| **Estimation** | `/v1/estimate`, `/v1/estimate/batch`, `/v1/estimates`, `/v1/estimates/{phase}` | ~250 total |
| **Mods introspection** | `/v1/mods` (list), `/v1/mods/stats`, `/v1/mods/cancellation_reasons`, `/v1/mods/generators`, `/v1/mods/phases`, `/v1/mods/phases/known`, `/v1/mods/phases/{phase_id}` | ~400 total |
| **Mods sub-resources** | `/v1/mods/{id}/metadata`, `/v1/mods/{id}/summary`, `/v1/mods/{id}/timeline`, `/v1/mods/{id}/t2_judges`, `/v1/mods/{id}/retry` | ~500 total |
| **Packs and route preview** | `/v1/packs`, `/v1/route_preview` | ~200 total |
| **Feature flags** | `/v1/feature_flags`, `/v1/feature_flags/history` | ~150 total |
| Total | 20 new endpoints | ~1500 lines of handlers + ~500 lines of Pydantic schemas |

### B. 50+ new feature generators (in `generators/packs/stardew_valley/features/`)

Master has 6 generators (texture, shop_channel, npc_schedule,
event_mod, custom_crafting, farm_expansion, plus the cross-cutting
ones in generators/core). Branch has 55+ feature directories
(achievements, animal_expansion, fishing_overhaul, weather_event,
tv_schedule, weapon_definition, witch_swamp, etc.). Each is
500-1500 lines of self-contained code, ~30,000 lines total.

**Important caveat:** the cron's round 22 PENDING_COMMIT
(`docs/CRON_RUN_ARCHIVE_2026-07-03.md`) explicitly says:
> Registering the phase without the generator would create a
> broken state where the override fires but the orchestrator
> crashes looking for a non-existent generator.

The generators and the orchestrator extensions are entangled:
adding a generator requires updating `generators/packs/stardew_valley/
__init__.py` to register the phase, AND the `feature_flags.py` set
(`_DEFAULT_FLAGS` key) so the orchestrator can gate it. So the
50+ generators come in **phases**, not in 50+ small commits.

### C. The 4 orchestrator extensions (in `orchestrator/`)

Branch's `orchestrator/` has:
- `router.py` +2270 lines (vs master's 203) — full P3 multi-keyword routing
- `state.py` +163 — extended state dataclass
- `pipeline.py` +415 — LangGraph state machine
- `feature_flags.py` +566 — already on master (cron round 1, expanded
  in rounds 2-22 to 510 lines, much closer to the 566 in the branch)

Master's router works (8 endpoints, weather priority), but the
branch's full router is needed for the 50+ generators to route
correctly. This is the same "broader P3-P5 stack" chicken-and-egg
the cron's PENDING_COMMIT_v22 flagged.

---

## Schedule (target: 5 sessions, 2-3 hours each)

### Session 1: Mods introspection (1 PR, ~1.5-2 hours)

**Pick:** 7 introspection endpoints that are read-only over existing
data. No new generators required.

**Files to port:**
- `app/api/routes.py`: 7 new endpoints
- `app/api/schemas.py`: ~7 Pydantic response models
- `storage/queries.py`: 3-5 new query functions (list_mod_requests,
  count_mod_requests, get_mod_request_stats, etc.)
- `tests/`: TestClient-based tests for each endpoint (with AsyncMock
  on the storage getters — the cron-diagnosis skill recipe)

**Pre-work:** I (the parent) will pre-stage the source bundle
`docs/_source_routes_app_api.py.txt` (the branch's routes.py) and
`docs/_source_schemas_app_api.py.txt` (the branch's schemas.py)
on master BEFORE the session starts. The session reads those via
read_file, ports the relevant functions, and runs pytest.

**Done when:** 7 endpoints work, tests green, merged to master,
pushed.

### Session 2: Estimation (1 PR, ~1 hour)

**Pick:** 4 estimation endpoints. They map cleanly to master's
existing `app/estimation.py` (already on master from the cron
archive, untouched but present). The endpoints expose the
estimation logic to clients.

**Files to port:**
- `app/api/routes.py`: 4 new endpoints
- `app/api/schemas.py`: ~4 Pydantic models
- `app/estimation.py`: minor docstring expansion (already on master)
- `tests/`: TestClient tests

**Done when:** 4 endpoints work, tests green.

### Session 3: Mods sub-resources (1 PR, ~2 hours)

**Pick:** 5 sub-resource endpoints (metadata, summary, timeline,
t2_judges, retry). These need a couple of new storage helpers
plus the T2 judges infrastructure (orchestrator feedback).

**Files to port:**
- `app/api/routes.py`: 5 new endpoints
- `app/api/schemas.py`: ~5 Pydantic models
- `storage/queries.py`: 2-3 new query functions
- `orchestrator/feedback_router.py`: minor docstring expansion
- `tests/`: TestClient tests for each

**Done when:** 5 endpoints work, tests green.

### Session 4: Packs + route preview (1 PR, ~1 hour)

**Pick:** 2 read-only endpoints. `/v1/packs` lists registered game
packs. `/v1/route_preview` is a dry-run of the router (no
generation, just routing decision). These are small.

**Files to port:**
- `app/api/routes.py`: 2 new endpoints
- `app/api/schemas.py`: ~2 Pydantic models
- `tests/`: TestClient tests

**Done when:** 2 endpoints work, tests green.

### Session 5: Feature flag admin endpoints (1 PR, ~1.5 hours)

**Pick:** 2 endpoints (`/v1/feature_flags`, `/v1/feature_flags/history`)
that expose the cron's `feature_flags.py` to clients. The source code
is already on master; we just need the HTTP layer.

**Files to port:**
- `app/api/routes.py`: 2 new endpoints
- `app/api/schemas.py`: ~2 Pydantic models
- `tests/`: TestClient tests with AsyncMock on the feature_flags module

**Done when:** 2 endpoints work, tests green.

**Total after Session 5:** 20 new endpoints live, ~1500+500=2000
new lines, ~30-50 new test cases.

### (Optional) Session 6: First batch of new feature generators (1 PR, ~3 hours)

**Pick:** 5-10 of the 50+ new generators, scoped to ONE phase family
(e.g., "fishing" — fishing_overhaul, fishponds, fish_definition,
fishing_overhaul alone is 1197 lines, so just one).

**Why optional:** Each generator needs the orchestrator extensions
to actually be useful end-to-end. Porting them as standalone files
without the routing updates creates dead code (per the cron's
PENDING_COMMIT_v22 caveat). The right path:

1. Port ONE generator + the matching phase registration + the
   matching router keyword.
2. End-to-end test: `POST /v1/mods/generate` with a prompt
   matching the new phase, verify the new generator runs.
3. Commit as a "first generator" PR.
4. Repeat for 4-9 more in subsequent sessions.

**Scope risk:** Each generator is 500-1500 lines. The cron's
source bundles are read-only, the cron subagent has file tools
but no shell. The pattern that worked (cron's 22 rounds) is the
right one for this too: parent pre-stages source bundle, cron
(or focused subagent in parent session) ports one generator
per round, parent verifies and commits.

---

## Pre-work for each session

Each session needs 5 source bundles staged on master BEFORE the
session starts. The parent (you or me, on a working network) runs:

```bash
# Get the source from the branch (these need shell access)
cd /home/hangyu5/Documents/Gitrepo-My/AMG

# Routes — the full routes.py (3936 lines, big)
git show discord-ops-hardening:sdv-mod-generator/app/api/routes.py \
  > sdv-mod-generator/docs/_source_routes_app_api.py.txt

# Schemas — the full schemas.py (2452 lines, big)
git show discord-ops-hardening:sdv-mod-generator/app/api/schemas.py \
  > sdv-mod-generator/docs/_source_schemas_app_api.py.txt

# Queries (already staged, refresh if needed)
git show discord-ops-hardening:sdv-mod-generator/storage/queries.py \
  > sdv-mod-generator/docs/_source_queries.py.txt

# Router (already staged)
git show discord-ops-hardening:sdv-mod-generator/orchestrator/router.py \
  > sdv-mod-generator/docs/_source_router.py.txt

# For Session 6 (generators): the chosen generator file
git show discord-ops-hardening:sdv-mod-generator/generators/packs/stardew_valley/features/fishing_overhaul/__init__.py \
  > sdv-mod-generator/docs/_source_fishing_overhaul.py.txt

# Commit the bundle(s)
git add sdv-mod-generator/docs/_source_*.py.txt
git commit -m "chore(docs): pre-stage source bundle for <session name>"
git push origin master
```

Without these bundles, the cron can't read the source. The
parent who has shell access must run this before the cron resumes
or before a parent-session extraction.

## Resume the cron for Sessions 1-5

Once a session's source bundles are staged, you can:

1. Resume the cron: `cronjob action=resume job_id=8faa6346fe1e`
2. The cron's prompt will read `docs/P3_P5_MERGE_PLAN.md` (this file,
   updated with the session's task) and pick the right small piece.
3. Each cron tick is one round, ≤200 lines, one endpoint or one
   helper. The parent verifies + commits + pushes on the next
   return.

For Session 6 (generators), the cron is probably the wrong tool —
generator files are 500-1500 lines each, way over the 200-line cap.
Sessions 6+ should be parent-session work, not cron.

## When to delete discord-ops-hardening

After Session 5, all 28 endpoints are on master. After Session 6
(if done), 5+ generators are on master. At that point, the
branch has 50-5=45 remaining generators but they're optional
content. The branch can be safely deleted; everything important
is on master.

If you want to keep the branch for archaeology (your earlier
choice), that's fine — no harm in leaving it.
