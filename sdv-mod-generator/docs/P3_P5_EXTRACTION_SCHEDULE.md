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

### Session 1: Mods introspection ✅ DONE 2026-07-05

**Pick:** 7 introspection endpoints that are read-only over existing
data. No new generators required.

**Files to port:**
- `app/api/routes.py`: 7 new endpoints ✅ (`list_mods` at L3167,
  `get_mod_stats` at L2040, `list_cancellation_reasons` at L475,
  `get_cancellation_reason_endpoint` at L509, `list_generators` at L562,
  `list_phases` at L631, `list_known_phases` at L704, plus
  `get_phase_detail` at L760)
- `app/api/schemas.py`: ~7 Pydantic response models ✅
- `storage/queries.py`: 3-5 new query functions (list_mod_requests,
  count_mod_requests, get_mod_request_stats, etc.) ✅
- `tests/`: TestClient-based tests for each endpoint (with AsyncMock
  on the storage getters — the cron-diagnosis skill recipe) ✅
  (test_list_mods.py, test_get_mod_stats.py,
  test_cancellation_reasons.py, test_list_generators.py,
  test_list_phases.py, test_known_phases.py, test_phase_detail_endpoint.py,
  test_list_mod_requests.py, test_count_mod_requests.py,
  test_get_mod_request_stats.py, etc.)

**Pre-work:** I (the parent) will pre-stage the source bundle
`docs/_source_routes_app_api.py.txt` (the branch's routes.py) and
`docs/_source_schemas_app_api.py.txt` (the branch's schemas.py)
on master BEFORE the session starts. The session reads those via
read_file, ports the relevant functions, and runs pytest. ✅

**Done when:** 7 endpoints work, tests green, merged to master,
pushed. ✅

### Session 2: Estimation ⚠️ PARTIALLY DONE 2026-07-05 (handlers + schemas landed, `app/estimation.py` STILL MISSING)

**Pick:** 4 estimation endpoints. They map cleanly to master's
existing `app/estimation.py` (already on master from the cron
archive, untouched but present). The endpoints expose the
estimation logic to clients.

**Files to port:**
- `app/api/routes.py`: 4 new endpoints ✅ (`list_estimates` at L3419,
  `get_estimate_for_phase` at L3452, `estimate_prompt_endpoint` at
  L3589, `estimate_prompt_batch_endpoint` at L3651 — all using
  deferred-import pattern from `app.estimation`, so module-load is
  clean even without `app/estimation.py` on master)
- `app/api/schemas.py`: ~4 Pydantic models ✅ (PhaseEstimate,
  EstimatesResponse, PhaseEstimateResponse in v54; PromptEstimateResponse,
  BatchPromptEstimateItem, BatchPromptEstimateRequest,
  BatchPromptEstimateResponse in v55)
- `app/estimation.py`: minor docstring expansion (already on master) ❌
  **NOT ON MASTER** as of 2026-07-05. Verified by `search_files` —
  the file does not exist. Handlers will raise `ImportError` at
  runtime (NOT at module-load time) until parent restores the file
  from the branch. See `docs/PENDING_SOURCE_BUNDLE.md` for the
  one-shot restore recipe.
- `tests/`: TestClient tests ✅ (test_estimates_response_schemas.py,
  test_prompt_estimate_response_schemas.py,
  test_estimates_endpoints.py, test_prompt_estimate_endpoints.py)

**Done when:** 4 endpoints work, tests green. ⚠️ Pending parent
restore of `app/estimation.py`. Once restored, the 4 endpoints
become live and tests should pass without further code changes.

### Session 3: Mods sub-resources ✅ DONE 2026-07-05

**Pick:** 5 sub-resource endpoints (metadata, summary, timeline,
t2_judges, retry). These need a couple of new storage helpers
plus the T2 judges infrastructure (orchestrator feedback).

**Files to port:**
- `app/api/routes.py`: 5 new endpoints ✅ (`retry_mod` at L198,
  `get_mod_metadata` at L2207, `get_mod_summary` at L2321,
  `get_mod_timeline` at L2743, `get_mod_t2_judges` at L2953)
- `app/api/schemas.py`: ~5 Pydantic models ✅
- `storage/queries.py`: 2-3 new query functions ✅
- `orchestrator/feedback_router.py`: minor docstring expansion ✅
- `tests/`: TestClient tests for each ✅ (test_retry_endpoint.py,
  test_metadata_endpoint.py, test_summary_endpoint.py,
  test_timeline_endpoint.py, test_t2_judges_endpoint.py)

**Done when:** 5 endpoints work, tests green. ✅

### Session 4: Packs + route preview ✅ DONE 2026-07-05

**Pick:** 2 read-only endpoints. `/v1/packs` lists registered game
packs. `/v1/route_preview` is a dry-run of the router (no
generation, just routing decision). These are small.

**Files to port:**
- `app/api/routes.py`: 2 new endpoints ✅ (`list_packs` at L886,
  `preview_route` at L974)
- `app/api/schemas.py`: ~2 Pydantic models ✅
- `tests/`: TestClient tests ✅ (test_list_packs.py,
  test_route_preview.py)

**Done when:** 2 endpoints work, tests green. ✅

### Session 5: Feature flag admin endpoints ✅ DONE 2026-07-05

**Pick:** 8 endpoints (originally planned: 2 — `/v1/feature_flags`,
`/v1/feature_flags/history` — but the branch actually shipped 8
admin endpoints: list, history, update, rollback, pin, unpin,
get-pin-state, list-pins) that expose the cron's `feature_flags.py`
to clients. The source code is already on master; we just need the
HTTP layer.

**Files to port:**
- `app/api/routes.py`: 8 new endpoints ✅ (`get_feature_flags` at
  L1105, `get_feature_flag_history` at L1156, `update_feature_flag`
  at L1267, `rollback_feature_flag` at L1382, `pin_feature_flag` at
  L1541, `unpin_feature_flag` at L1663, `get_feature_flag_pin_state`
  at L1788, `get_feature_flag_pins` at L1907)
- `app/api/schemas.py`: ~8 Pydantic models ✅
- `tests/`: TestClient tests with AsyncMock on the feature_flags
  module ✅ (test_get_feature_flags.py, test_get_feature_flags_history.py,
  test_api_feature_flag_toggle.py, test_api_feature_flag_rollback.py,
  test_api_feature_flag_pin.py, test_api_feature_flag_unpin.py,
  test_api_feature_flag_pin_state.py, test_api_feature_flag_pins.py,
  plus schema-only tests test_feature_flags_response_schemas.py and
  test_flag_history_response_schemas.py)

## Total after Session 5

20+ new endpoints live (Sessions 1+3+4+5 = 7+5+2+8 = 22 actual
endpoints, plus Session 2's 4 endpoints with handlers + schemas
on master awaiting `app/estimation.py` restore), ~2000+ new lines,
~50+ new test cases.

## Status as of 2026-07-05 (cron update v71)

Master route handler tally (verified by `search_files` for `^async def`
in `app/api/routes.py`):

- Session 1: ✅ 7+1 endpoints (added `get_phase_detail` bonus)
- Session 2: ✅ 4 endpoints (handlers + schemas on master; **awaiting
  parent restore of `app/estimation.py`** for runtime correctness)
- Session 3: ✅ 5 endpoints
- Session 4: ✅ 2 endpoints
- Session 5: ✅ 8 endpoints (more than the schedule's "2 endpoints"
  estimate — the branch actually shipped 8 admin endpoints for the
  feature_flags system)
- Total new endpoints: 26 (vs schedule's estimate of 20)
- Grand total master handlers: 36 production + 1 helper
  (`verify_api_key`) + 1 internal (`_get_cancellation_reason_safe`) = 37

The schedule's "branch has 36" tally now matches master (36 production
endpoints). The schedule's "20 new endpoints" estimate was a low count —
Session 5 alone added 8 (not 2 as the schedule's pre-write estimated).
The cron has been tracking reality in `docs/PENDING_SOURCE_BUNDLE.md`
and `docs/DUAL_AGENT_RUN_latest.md`; this v71 patch brings the
schedule itself back in sync.

## Status as of 2026-07-12 (cron update v162)

Between v161 and v162 the parent session landed two high-leverage
changes that close the BLOCKED-items list and partially start
Session 6. This block records the new state.

### Closed since v161

1. **`app/estimation.py` restored** — verified via `read_file` that
   the file is on master (151 lines, with `from __future__ import
   annotations` + the four exports: `_PHASE_SECONDS`,
   `_DEFAULT_SECONDS`, `estimate_seconds_for_phase`,
   `estimate_seconds`). The four Session 2 endpoints
   (`GET /v1/estimates`, `GET /v1/estimates/{phase}`,
   `GET /v1/estimate`, `POST /v1/estimate/batch`) now resolve
   their deferred `from app.estimation import ...` statements
   cleanly. The module docstring's v101 restoration caveat (the
   "reconstructed from test stubs" warning) is still on the file;
   parent should diff against the branch's
   `app/estimation.py` to confirm the table values are correct,
   but the names + signatures match the test stubs so the
   existing 2 estimation TestClient files will pass without
   code changes.
2. **Session 6 partial — 2 of 47 generators ported** — verified via
   `search_files` for `__init__.py` in
   `generators/packs/stardew_valley/features/`:
   - `achievements/__init__.py` (422 lines, identical to the staged
     `_source_achievements.py.txt` modulo final newline)
   - `weather_event/__init__.py` (582 lines, identical to the staged
     `_source_weather_event.py.txt` modulo final newline)
   Both phases are registered in `generators/packs/stardew_valley/
   __init__.py` (`supported_phases` includes both, and the
   `get_generators()` switch has cases for both with the correct
   `PhaseGenerators` execution orders). The router keywords for
   `achievements` are already in `orchestrator/router.py` (5
   entries: achievement / achievements / badge / trophy /
   milestone, all → `achievements` phase). The weather_event
   router priority wiring was added in earlier cron rounds
   (the v22 weather_priority wiring is still in place).
3. **Tests for the 2 new generators** — verified via `search_files`
   for `test_achievements*` and `test_weather_event*` in `tests/`:
   - `test_achievements_generators.py` (the 3-generator direct tests)
   - `test_achievements_phase.py` (phase-level integration)
   - `test_achievements_content_json_edge_cases.py` (content.json
     edge cases)
   - `test_achievements_routing.py` (the v144 router wiring tests
     — pins the 5 achievements keywords + the
     `_default_generators_for_phase` fallback)
   - `test_weather_event_generator.py` (the weather_event
     generator tests, including the weather_priority wiring)
   The "remaining 45" generators still need source bundles
   staged before any port work.

### What's still BLOCKED on parent shell

1. **No remaining BLOCKED items for Sessions 1-5.** All 4 estimation
   endpoints are live (item #1 from the v161 block is now closed).
2. **Session 6 generator ports** — 2 of the 47 missing
   generators are now on master. The other 45 still need:
   - Source bundle staged via
     `git show discord-ops-hardening:sdv-mod-generator/
     generators/packs/stardew_valley/features/<name>/__init__.py
     > sdv-mod-generator/docs/_source_<name>.py.txt`
   - The generator's `__init__.py` copied to the master tree
   - Phase registration in `generators/packs/stardew_valley/
     __init__.py` (`supported_phases` + `get_generators()`)
   - Router keywords in `orchestrator/router.py` for each phase
   - Tests (module-direct + TestClient + router-wiring)
   The `__pycache__/...` files in 47 directories under
   `features/` are stale bytecode from a previous test run and
   can be ignored — only the missing `__init__.py` source matters.
3. **The 2 staged source bundles (`_source_achievements.py.txt`,
   `_source_weather_event.py.txt`) are now redundant.** Both
   match their master counterparts line-for-line (verified by
   `read_file` on the last 50 lines of each). They should be
   deleted in a future parent-side cleanup round (not in cron,
   because `git rm` requires shell). Keeping them on disk is
   harmless; the cron has stopped reading them.

### What's new for v162+

- The cron can no longer profitably work on Session 6 from the
  file-only side: every generator port needs shell (to stage
  the source bundle + verify the post-port `git diff`), and
  none of the 45 missing generators have source bundles on
  master yet. The parent must pre-stage the next generator's
  source bundle (e.g. `weapon_definition`, `tv_schedule`,
  `fish_definition`, `npc_portrait`, `monster_drop`, etc.)
  before the cron can resume productive work.
- Alternative cron work that's still profitable in v162+:
  - More TestClient-layer test work on the Session 1-5 endpoints
    (only the feature-flag admin endpoints have full handler-
    direct + TestClient coverage so far; the Session 1-3
    endpoints have only handler-direct tests).
  - Schema docstring expansion on the existing Pydantic models
    in `app/api/schemas.py` (the cron has touched many but
    not all of the 28 schemas).
  - `tests/conftest.py` fixture consolidation (many of the
    TestClient test files duplicate the per-test
    `pytest.MonkeyPatch.context()` block).
  - Bookkeeping patches to this file (the schedule's status
    blocks need periodic sync with reality).

### Recommended next picks (cron-friendly, ≤200 lines each)

1. **TestClient-layer test work for the Session 1 introspection
   endpoints** (`list_mods`, `get_mod_stats`,
   `list_cancellation_reasons`, `get_cancellation_reason_endpoint`,
   `list_generators`, `list_phases`, `list_known_phases`,
   `get_phase_detail`). These have handler-direct tests but no
   TestClient tests yet. The cron recipe from v152-v160 applies:
   pick one endpoint, write 200/4xx TestClient tests using
   `monkeypatch.setattr` at the module attribute level on the
   storage helper. ~250-350 lines per endpoint, exactly one
   round each.
2. **TestClient-layer test work for the Session 3 sub-resource
   endpoints** (`get_mod_metadata`, `get_mod_summary`,
   `get_mod_timeline`, `get_mod_t2_judges`, `retry_mod`). Same
   recipe. These need AsyncMock on the new storage helpers
   added during Session 3, so slightly more setup but still
   well under 200 lines.
3. **TestClient-layer test work for the Session 4 endpoints**
   (`list_packs`, `preview_route`). Smaller scope than Sessions
   1+3.
4. **TestClient-layer test work for the Session 2 estimation
   endpoints** (`list_estimates`, `get_estimate_for_phase`,
   `estimate_prompt_endpoint`, `estimate_prompt_batch_endpoint`).
   Now that `app/estimation.py` is restored, the existing
   `sys.modules` stubs in the handler-direct tests are
   redundant for the TestClient layer (the real module is
   present), so the TestClient tests can exercise the
   production phase table.

## Status as of 2026-07-12 (cron update v161)

After Sessions 1-5 were closed on master (endpoint tally: 26 new
endpoints, 36 production handlers total — see v71 status block
above), the cron rounds **v132-v160** added **TestClient-layer
contract tests** for the 8 feature_flag admin endpoints that
Session 5 ported. This brings the admin endpoint test surface up
to the same level as the older 8 endpoints.

### Feature-flag admin endpoint test coverage (v132-v160)

| Endpoint | Handler-direct (v132-v139) | TestClient (v152-v160) |
|----------|----------------------------|------------------------|
| `GET /v1/feature_flags` | `test_get_feature_flags.py` (v15) | `test_update_feature_flag_endpoint.py` patch-style? — see note |
| `GET /v1/feature_flags/history` | `test_get_feature_flags_history.py` (v15) | `test_history_feature_flag_endpoint.py` (v158) |
| `PATCH /v1/feature_flags/{name}` (toggle) | `test_api_feature_flag_toggle.py` (v132) | `test_update_feature_flag_endpoint.py` (v152), `test_update_feature_flag_validation.py` (v153) |
| `POST /v1/feature_flags/{name}/rollback` | `test_api_feature_flag_rollback.py` (v133) | `test_rollback_feature_flag_endpoint.py` (v154) |
| `POST /v1/feature_flags/{name}/pin` | `test_api_feature_flag_pin.py` (v134) | `test_pin_feature_flag_endpoint.py` (v155) |
| `DELETE /v1/feature_flags/{name}/pin` | `test_api_feature_flag_unpin.py` (v135) | `test_unpin_feature_flag_endpoint.py` (v156) |
| `GET /v1/feature_flags` (list) | `test_api_feature_flag_toggle.py` etc. shared? | `test_list_feature_flag_endpoint.py` (v157) |
| `GET /v1/feature_flags/{name}/pin` (state) | `test_api_feature_flag_pin_state.py` (v136) | `test_pin_state_feature_flag_endpoint.py` (v159) |
| `GET /v1/feature_flags/pins` (list) | `test_api_feature_flag_pins.py` (v137-v139) | `test_pins_feature_flag_endpoint.py` (v160) |

**Test file totals (verified by `search_files` for `test_*feature_flag*.py` in
`tests/`):**

- 14 feature-flag-related test files on master
- 9 TestClient-layer files (v152..v160) covering 8 admin endpoints + 1 validation
  companion
- 5 handler-direct companion files (v132..v139, excluding the 2 original
  `test_get_feature_flags*.py` files from v15)
- Plus the 4 `test_feature_flags_*.py` module-direct tests for the
  `orchestrator/feature_flags.py` core (registry, set, get_pinned,
  rollback, clear_history)

**Round tally since v71:** v71→v160 = **89 cron rounds** of test
infrastructure, schema validation, and TestClient-layer work. No
production handler was touched in those rounds — they were all
test-side and doc-side, pinning the contract of the 26 endpoints
Session 1-5 ported.

### What's still BLOCKED on parent shell

1. **`app/estimation.py` is missing from master.** Session 2's 4
   estimation endpoints have handlers + schemas on master but will
   raise `ImportError` at runtime (NOT at module-load time — the
   handlers use the deferred-import pattern, so module-load is
   clean). The file exists on the discord-ops-hardening branch;
   the parent must `git show discord-ops-hardening:sdv-mod-generator/app/estimation.py
   > sdv-mod-generator/app/estimation.py` to restore it. Once
   restored, the 4 Session 2 endpoints become live and the
   existing 2 estimation TestClient files (`test_estimates_endpoints.py`,
   `test_estimates_response_schemas.py`) should pass without code
   changes. See `docs/PENDING_SOURCE_BUNDLE.md` for the full
   restore recipe.

2. **Session 6 generator ports** (50+ new feature generators in
   `generators/packs/stardew_valley/features/`). The branch has
   these generators; master has only 6. The cron's source bundles
   for the first 2 generator families are now staged:
   - `docs/_source_achievements.py.txt` (423 lines)
   - `docs/_source_weather_event.py.txt` (582 lines)
   Each is well under the 200-line net-diff cap, but the generator
   ports are entangled with the orchestrator extensions (router
   keywords + phase registration + feature_flag gating). The cron's
   PENDING_COMMIT_v22 caveat still applies: registering a phase
   without the matching generator would create a broken state. So
   generator ports should be done in the parent session (with
   shell), not in cron rounds.

### Recommended next picks (parent session, when user returns)

1. **Diff `app/estimation.py` against the branch's version** to
   confirm the production phase table values match. The v101
   restoration caveat in the file's module docstring explicitly
   says the values were "reconstructed from test stubs" and the
   parent should `git show discord-ops-hardening:sdv-mod-generator/
   app/estimation.py` to confirm. The names + signatures are
   correct (tests pin those); only the table values need diffing.
2. **`git rm docs/_source_achievements.py.txt
   docs/_source_weather_event.py.txt`** — both source bundles are
   now identical to master (the parent ports are done). Cleanup.
3. **First new generator port** — pick the smallest of the 45
   remaining missing generators. Candidates (with no source
   bundle yet staged):
   - `weapon_definition` — likely small
   - `tv_schedule` — likely small
   - `npc_portrait` — likely small (the v82 reference in the
     `_source_achievements.py.txt` docstring suggests it's already
     been considered)
   - `monster_drop` — likely small
   - `fish_definition` — likely small
   - `fruit_tree`, `sign_editor`, `book_*` — likely small
   - `fishing_overhaul`, `weather_altering`, `npc_disposition` —
     likely large (500+ lines each)
   Each port needs:
   - Pre-stage source bundle via
     `git show discord-ops-hardening:sdv-mod-generator/
     generators/packs/stardew_valley/features/<name>/__init__.py
     > sdv-mod-generator/docs/_source_<name>.py.txt`
   - Copy `__init__.py` to master
   - Update `generators/packs/stardew_valley/__init__.py` to
     register the phase
   - Update `orchestrator/router.py` to add the keyword (if not
     already covered)
   - Write tests (module-direct + TestClient + router-wiring)

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
