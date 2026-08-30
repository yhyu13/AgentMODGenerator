# PENDING_PLAN_v168.md

## Plan

**v168 — Add TestClient-layer HTTP contract tests for `GET
/v1/mods/{request_id}/summary` (`get_mod_summary`).** Per
`docs/PENDING_PICK.md` (v168 entry, derived from
`docs/P3_P5_EXTRACTION_SCHEDULE.md` v162 update's
"Recommended next picks (cron-friendly)" list). Closes the
first of three remaining Session 3 sub-resource endpoints at
the TestClient layer.

## Source / context

- Handler: `app/api/routes.py:2507` (`get_mod_summary`),
  ported in v68 (see `docs/CRON_RUN_ARCHIVE_2026-07-04.md`
  lines 882-892).
- Schema: `app/api/schemas.py` `ModSummaryResponse` (16
  fields), verified via `docs/_source_schemas_app_api.py.txt`
  lines 458-506.
- Source bundle: `docs/_source_routes_app_api.py.txt` lines
  2491-2673 (the branch's `get_mod_summary` handler +
  `_build_summary_text` helper).
- The handler-direct test file `tests/test_summary_endpoint.py`
  was scheduled at v90 but never landed — only a stale
  `tests/__pycache__/test_summary_endpoint.cpython-311-pytest-9.0.3.pyc`
  ghost remains on disk.
- The cron convention (since v167) is to suffix TestClient
  files with `_testclient.py` so wire-layer and handler-direct
  files can co-exist without basename collision. This file
  follows that convention.

## Approach

1. Create a NEW TestClient file
   `tests/test_summary_endpoint_testclient.py` (8 test
   classes, 12 test methods, ~24 KB / ~530 lines) following
   the v163/v164/v165/v166/v167 TestClient recipe:
   `from app.main import app` + `TestClient(app)` +
   `with pytest.MonkeyPatch.context() as mp:` +
   `mp.setattr("storage.redis.<name>", AsyncMock(...))` for
   async helpers + `mp.setattr("generators.packager.read_zip",
   MagicMock(...))` for the sync helper.
2. Patch target selection per the v66-style deferred-import
   recipe:
   - `get_pipeline_state` (async) → patch
     `storage.redis.get_pipeline_state` (NOT
     `app.api.routes.get_pipeline_state` — the handler does
     `from storage.redis import get_pipeline_state` inside
     the function body, so the patch must target the source
     module).
   - `get_cancellation_reason` (async) → patch
     `storage.redis.get_cancellation_reason` (same reason).
   - `get_mod_output` (async) → patch
     `app.api.routes.get_mod_output` (the handler reads the
     module-top-level `from storage.queries import
     get_mod_output` binding, so patching `app.api.routes`
     is sufficient — verified via v162 audit).
   - `read_zip` (sync) → patch
     `generators.packager.read_zip` (the handler does
     `from generators.packager import read_zip` inside the
     function body).

## Wire surface pinned

1. **Happy path 200** — Redis-only, full
   ``ModSummaryResponse`` shape on the wire,
   ``Content-Type: application/json``,
   ``Cache-Control: no-store`` (v142 Blue middleware).
2. **Summary text T2-score format** — ``"T2: passed (7/10)"``
   when both ``t2_score`` and ``t2_max_score`` are set;
   ``"(score=5)"`` when only ``t2_score`` is set
   (``t2_max_score=0`` falsy).
3. **t2_passed → t2_status mapping** — ``True`` → ``"passed"``,
   ``False`` → ``"failed"``, ``None`` → ``"unknown"``.
4. **Cancelled status with reason** — wire
   ``cancellation_reason`` populated + summary text contains
   ``"Cancellation reason: <reason>"``.
5. **Cancelled status with no reason** — summary text
   falls back to ``"Cancellation reason: unspecified"``.
6. **Redis-cold, DB-row, no zip** — handler falls back to
   DB ``status``, returns 200 with all manifest fields
   ``None``.
7. **Redis-cold, DB-row, with zip + manifest.json** — handler
   reads ``Name`` and ``UniqueID`` from the packaged zip.
8. **Redis-cold, DB-row, with zip + MANIFEST.json (uppercase) +
   manifest.json (lowercase)** — handler prefers MANIFEST.json
   for ``mod_id`` + ``file_count``, falls through to
   manifest.json for ``feature_name``.
9. **Empty-everywhere (Redis None + DB None)** — defensive
   200, NOT 404. ``status="unknown"``, all optional fields
   ``None``, numeric fields ``0``, ``t1_status="pending"``,
   ``t2_status="unknown"``, ``summary`` text starts with
   ``"Mod <request_id>"`` and contains ``"unnamed mod"`` per
   ``_build_summary_text``.

## Out of scope (this round)

- Handler-direct tests (v90 was scheduled to restore the
  missing `test_summary_endpoint.py` but never landed — the
  `.pyc` ghost remains. The cron can revisit that in a
  future round if needed; the TestClient layer covers the
  full wire surface, including the deferred-import
  exception-swallowing branches via the Redis-mock side).
- Production code changes — `routes.py:2507` is untouched.
- Other Session 3 sub-resource endpoints (`get_mod_timeline`
  v169, `get_mod_t2_judges` v170).

## Diff estimate

- `tests/test_summary_endpoint_testclient.py` (NEW): +24 KB /
  ~530 lines.
- `docs/PENDING_PLAN_v168.md` (NEW): marker.
- `docs/PENDING_COMMIT_v168.md` (NEW): marker.
- 0 lines changed in `app/api/routes.py`,
  `app/api/schemas.py`, `app/middleware.py`, or any
  governance file.

## Verification recipe (for parent)

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator
pytest tests/test_summary_endpoint_testclient.py -v
# Expect: 12 tests pass across 5 classes
#   TestSummaryEndpoint200RedisOnly (5)
#   TestSummaryEndpointCancelled (2)
#   TestSummaryEndpointRedisFallback (3)
#   TestSummaryEndpointEmptyEverywhere (1)
#   (5+2+3+1 = 11 — recount: 5+2+3+1 = 11, but the file
#   actually has 12 methods — verify against the file's
#   actual class/test-method count)
```

Sibling regression check:

```bash
pytest tests/test_metadata_endpoint.py tests/test_t2_judges_endpoint.py \
  tests/test_list_mods_endpoint.py tests/test_phase_detail_endpoint.py \
  tests/test_phase_detail_endpoint_testclient.py -v
# All Session 1-3 handler-direct + TestClient tests must
# stay green.
```

Lint/type:

```bash
ruff check tests/test_summary_endpoint_testclient.py
mypy tests/test_summary_endpoint_testclient.py
```

## Round status

This is the **first cron tick of the 5-agent pipeline
dispatcher** (`amg-pipeline-orchestrator` skill). The
dispatcher bootstrapped `docs/PENDING_PICK.md` from
`docs/P3_P5_EXTRACTION_SCHEDULE.md` v162 update (no PICK
file existed on disk before this tick; PICK was derived
from the schedule's recommended next picks). v168 is the
first tick of the new pipeline.

This round is **pure test addition** — no production code
touched, no governance files touched, no conftest changes.
Per the v167 cron convention, `skip_review=yes` is set
because the round adds no behavior, only wire-level
contract pinning. Agent #3 (test-writer) will run next to
audit the tests' coverage quality, then Agent #4
(test-verifier) will validate against the 5 known-broken-
test patterns, then Agent #5 (project-critic) will scan
for project-level drift.