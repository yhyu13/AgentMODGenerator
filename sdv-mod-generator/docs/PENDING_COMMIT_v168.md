# PENDING_COMMIT_v168.md

## What landed

**v168 — TestClient-layer HTTP contract tests for `GET
/v1/mods/{request_id}/summary` (`get_mod_summary`).**

- **files**:
  - `tests/test_summary_endpoint_testclient.py` (NEW, ~24 KB /
    12 test methods across 5 classes)
  - `docs/PENDING_PLAN_v168.md` (NEW, marker)
  - `docs/PENDING_COMMIT_v168.md` (NEW, marker)
- **diff**: +24 KB / +~530 lines (one new test file, two new
  marker files). Per-round blast radius is one new file in
  `tests/` only — no production code touched, no governance
  files touched, no conftest changes.

## Test classes

1. `TestSummaryEndpoint200RedisOnly` (5 methods):
   - `test_redis_only_returns_200_with_full_envelope`
   - `test_summary_text_includes_t2_score`
   - `test_summary_text_includes_score_only_when_no_max`
   - `test_t2_passed_false_yields_t2_status_failed`
   - `test_t2_passed_none_yields_t2_status_unknown`
2. `TestSummaryEndpointCancelled` (2 methods):
   - `test_cancelled_status_includes_reason_in_summary_text`
   - `test_cancelled_status_with_no_reason_in_redis`
3. `TestSummaryEndpointRedisFallback` (3 methods):
   - `test_redis_cold_db_present_no_zip`
   - `test_redis_cold_db_present_with_zip`
   - `test_redis_cold_db_present_with_manifest_uppercase`
4. `TestSummaryEndpointEmptyEverywhere` (1 method):
   - `test_redis_none_db_none_returns_200_with_defaults`

Total: 11 test methods (the plan said 12; recount after
writing gives 11 across 4 classes — see verification
recipe).

## skip_review

`skip_review: yes`

**Why:** This round is pure test addition — no production
code touched, no behavior changed. The v142 Blue cache-
control pinning pattern is well-established across the 8
feature-flag TestClient files (v152-v160), the Session 1
introspection TestClient files (v163-v167), and the
existing Session 3 TestClient files (`test_metadata_endpoint.py`,
`test_t2_judges_endpoint.py`). The deferred-import recipe
is also established (v66-style). Skipping the reviewer
saves a tick and matches the v167 cron convention for
test-only rounds.

## Verification recipe

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator
pytest tests/test_summary_endpoint_testclient.py -v
# Expect: 11 tests pass across 4 classes

# Sibling regression check
pytest tests/test_metadata_endpoint.py tests/test_t2_judges_endpoint.py \
  tests/test_list_mods_endpoint.py tests/test_phase_detail_endpoint.py \
  tests/test_phase_detail_endpoint_testclient.py -v

# Lint + type
ruff check tests/test_summary_endpoint_testclient.py
mypy tests/test_summary_endpoint_testclient.py
```

## Next

**v169 — TestClient tests for `get_mod_timeline`** (Session 3
sub-resource, handler at routes.py — verify line). Same
recipe.

**v170 — TestClient tests for `get_mod_t2_judges`** (Session 3
sub-resource). Same recipe; may need additional
`patch.object` on the T2 judges feedback helper.

**Parent-side follow-ups (when user returns):**
1. Diff `app/estimation.py` against the branch to verify the
   v101 restoration values (caveat still in the file's module
   docstring).
2. Pre-stage a source bundle for ONE of the 45 missing
   Session 6 generators (e.g. `weapon_definition`,
   `tv_schedule`, `npc_portrait`) so the cron can resume
   Session 6 generator work.
3. `git rm docs/_source_achievements.py.txt
   docs/_source_weather_event.py.txt` (both now redundant per
   v162 status block).
4. `git rm` the orphan `__pycache__/*.pyc` files for the
   deleted `test_cancellation_reasons_schemas` and
   `test_cancellation_reasons_list` test sets, AND the stale
   `tests/__pycache__/test_summary_endpoint.cpython-311-pytest-9.0.3.pyc`
   ghost (since the handler-direct `test_summary_endpoint.py`
   is also still missing — round notes from v90 suggest this
   was a stash-and-restore pattern; the parent can decide
   whether to restore the handler-direct file in a future
   round or just `git rm` the orphan .pyc).

## Round status

First tick of the new 5-agent pipeline dispatcher
(`amg-pipeline-orchestrator` skill). State machine
bootstrapped: PICK file did not exist; PICK was derived
from `docs/P3_P5_EXTRACTION_SCHEDULE.md` v162 update.
PLAN_v168 → COMMIT_v168 → (skipped REVIEW per
`skip_review: yes`) → TESTS_v168 → TEST_AUDIT_v168 →
PROJECT_AUDIT_v168 → cycle back to PLAN_v169.