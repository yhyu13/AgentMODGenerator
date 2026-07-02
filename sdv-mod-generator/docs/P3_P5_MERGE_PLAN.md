# P3-P5 Merge Plan: extracting value from discord-ops-hardening

## Current state (as of 2026-07-03, end of session)

- `master` at 83a56fe, 312/312 tests pass, pushed to origin
- `discord-ops-hardening` at dfb3dd7, untouched in this session (still has the 18 commits with cron noise)
- `dual-agent-continuous` cron job (id `8faa6346fe1e`) updated with a new prompt: silent-exit when shell is blocked, real work (≤200 lines/commit, one round per tick, branch-prefix `cron/<timestamp>`) when shell works. Next run 2026-07-03 ~07:12 UTC+8.

### Session progress (2026-07-01 → 2026-07-02)

| PR | What landed | Commit on master | Tests | Lines |
|----|------------|------------------|-------|-------|
| 1 | Discord Ed25519 signature verification (real, not stub) | 8af9d90 (1a86065) | +0 then +10 (test follow-up) | +240/-571 |
| 1 | DM completion notifier (Redis watcher + Discord DM) | 1a86065 | +0 then +21 (PR 4c) | (above) |
| 1 | Dead-code cleanup: removed `discord/commands.py` | dcd6e89 | +0 | (above) |
| 1 | BadSignature import fix (real bug caught) | 8af9d90 (squashed fixup) | covered by signature test | (above) |
| 2 | Router weather_event priority override | eb2dd7d | +6 | +14 |
| 1+ test | Ed25519 verify_signature test coverage | 2bd6543 | +10 | +180 |
| 4a | SecurityHeadersMiddleware (13 OWASP headers + env gates) | 68d8be7 | +24 | +196 |
| 4c | DM notifier test coverage (the 0% gap from PR 1) | 868157f | +21 | +558 |

**Total: 5 PRs / 7 commits, +61 tests, 0 regressions.**

The discord-ops-hardening branch is now mostly drained of its high-value content. The 3 pre-cron Discord commits (dcd6e89, 685ef37, 873ae97) are on master. The router weather priority (487d924) is on master. The SecurityHeadersMiddleware (a1465cd) is on master (re-implemented cleanly from scratch, not cherry-picked). What's left in the branch is mostly the cron noise that we explicitly chose to skip.

---

## What the branch actually contains (verified, not guessed)

The 18 commits on discord-ops-hardening break down as follows. **Bold = extracted to master. Strike = noise deliberately skipped.**

| # | Commit | Layer | Status |
|---|--------|-------|--------|
| 1 | 2b9df9d | Discord | **EXTRACTED in PR 1** (dead-code cleanup) |
| 2 | 685ef37 | Discord | **EXTRACTED in PR 1** (Ed25519 + BadSignature fix) |
| 3 | 873ae97 | Discord | **EXTRACTED in PR 1** (DM notifier) |
| 4 | cb790d4 | Full snapshot | ~~wip baseline — pre-cron snapshot, untested~~ |
| 5 | 9487db8 | Full snapshot | ~~wip: 3-day cron accumulation, mostly noise~~ |
| 6 | 3d27cd5 | Tests | ~~repair unparseable test files (not relevant to master)~~ |
| 7 | 553cd6d | Docs | ~~DIAG noise from blocked cron sessions~~ |
| 8 | 487d924 | Router | **EXTRACTED in PR 2** (weather priority) |
| 9 | 20b30b6 | Docs | ~~DIAG noise~~ |
| 10 | a1465cd | All prod | **PARTIALLY EXTRACTED in PR 4a** (SecurityHeadersMiddleware re-implemented from scratch). Storage/quality/api all skipped — see below. |
| 11 | 4d6bf7d | Tests | ~~12 clean test files — unverified, skipped until reviewed individually~~ |
| 12 | 4265aff | Tests | ~~16 partial — known contract drift, skipped~~ |
| 13 | f3d74d3 | Tests | ~~20 need service mocks — skipped (not transportable)~~ |
| 14 | b95c935 | Docs | ~~84 stale PENDING_COMMIT markers (already removed earlier)~~ |
| 15 | 677a53f | Tests | ~~envelope assertions — superseded by the 10 Ed25519 tests in 2bd6543~~ |
| 16 | d07676b | Tests | ~~8 test fixes — already on master via earlier commit~~ |
| 17 | e291c4d | main.py | 422 handler fix — **superseded by 36bd9c2** (PR 1 already addressed the same class of bug) |
| 18 | dfb3dd7 | routes.py | route prefix fix — already in `a1465cd`, not transportable without the rest of the feature_flag stack |

**Result: 4 of 18 commits fully extracted, 1 partially extracted. The branch can be deleted after a final review.**

---

## Why PR 4b and 4c (storage + quality) were skipped

**PR 4b (storage) — skipped.** Master already has substantial storage code:
- `postgres.py` (92 lines): engine, session factory, init_db, close_pool
- `redis.py` (137 lines): client, pipeline_state, status, **plus the 3 DM-notifier functions from PR 1**
- `s3.py` (116 lines): client, key validation, local fallback, upload, download, presigned URL
- `queries.py` (137 lines): create_mod_request, update_status, save_output, get_output, get_user_history

The branch's `9487db8` adds 5 more query functions (`list_mod_requests`, `count_mod_requests`, `get_mod_request_stats`, `delete_old_mod_requests`, `is_valid_mod_status`) — but each is tied to a specific endpoint in the branch's routes.py that master doesn't have. Porting the query functions in isolation creates dead code; porting the endpoints requires the rest of the P3-P5 stack. Neither is a small win.

**PR 4c (quality gates) — skipped as a port, but a test-only PR was done instead.** The branch's `a1465cd` changes to `gate_t1.py` and `gate_t2.py` are 95% docstring additions. The only functional change is `logger = structlog.get_logger()` → `structlog.get_logger(__name__)` (trivially safe to skip) plus a `is_enabled("t2_three_judge_panel")` feature-flag gate in gate_t2 that requires a brand-new `orchestrator/feature_flags.py` module (567 lines on the branch). Not worth a PR.

**PR 4c (DM notifier tests) — DONE.** This was the 0% coverage gap I flagged after PR 1. Wrote 21 cases covering `_safe_fetch_user`, `_fire_success`, `_fire_failure`, `_tick`, and the `_run` / start / stop lifecycle. All AsyncMock fixtures, no real Redis/Discord required.

---

## What remains to extract from the branch

The branch is mostly drained. The remaining work falls into three buckets:

### A. Test files from the cron (commits 11, 12, 13)

`4d6bf7d` adds 12 "clean" test files. `4265aff` adds 16 "partial" files. `f3d74d3` adds 20 files that need service mocks. The cron generated these without review, so I haven't verified any of them.

**Recommendation:** Don't cherry-pick. Instead, for each test file, read it, check it tests something real, and port it as a fresh commit if valuable. The 3 commits collectively add 48 test files — likely 5-10% are valuable, the rest are noise. Manual triage would take 2-3 hours.

### B. The 28 new API endpoints (in the branch's routes.py)

Master has 8 endpoints. The branch has 36. The 28 new ones are P3-P5 features: `/v1/mods/stats`, `/v1/mods/{id}/timeline`, `/v1/mods/{id}/t2_judges`, `/v1/mods/{id}/metadata`, `/v1/mods/{id}/summary`, `/v1/feature_flags` (and 7 siblings), `/v1/feature_flag/{name}/pin`, `/v1/feature_flag/{name}/rollback`, `/v1/feature_flag/{name}/unpin`, `/v1/feature_flag/pins`, `/v1/feature_flag/history`, etc. Each one needs the supporting storage query functions and (for the feature_flag family) the `orchestrator/feature_flags.py` module.

**Recommendation:** Not in scope of "extract value from discord-ops-hardening." This is the P3-P5 phase work proper, which the merge plan originally called PR 4d-4f. Each endpoint is a multi-hour PR. Schedule for future sessions.

### C. The new generator files (in the branch's generators/packs/stardew_valley/features/)

Master has 6 feature generators. The branch has 50+. The new ones (`weather_event`, `weapon_definition`, `tv_schedule`, `witch_swamp`, etc.) are the P3 generator content. Each is 500-1500 lines of self-contained code that needs the orchestrator infrastructure from PR 4 to actually run.

**Recommendation:** Not extractable without PR 4's orchestrator rewrite. Schedule for after PR 4a-f.

---

## Recommended action: delete the branch

After 5 PRs of extraction work, the branch has ~5% residual value (the unverified test files in commits 11-13, mostly). The rest is either already on master or noise.

**Steps:**

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG
git checkout master
git pull  # if you have push access
git branch -D discord-ops-hardening  # delete the local branch
git remote prune origin              # clean up remote tracking refs (no-op if no remote branch)
```

The branch is local-only (no `remotes/origin/discord-ops-hardening`), so the second command is a no-op.

**Caution:** The branch is referenced by `AGENTS.md`'s "Recent commits" section and by the cron job `dual-agent-continuous` which has been working off it. After deletion, the cron job will need its workdir updated (or it can be retired — the session established that the cron is producing nothing useful under the strict prompt anyway).

---

## What this plan was wrong about (lessons)

The original plan called for 6 PRs:
- PR 1, 2, 3: small targeted fixes (kept the structure)
- PR 4a-4f: big re-implementation of a1465cd (REVISED — see below)
- PR 5, 6: test hardening + cleanup (deferred)

**Original estimate:** 8 sessions, 10-12 hours of focused work.

**Actual:** 1 session, ~5 PRs done. The session was able to extract more value faster than estimated because:

1. **The branch's "65k lines" turned out to be 1500 lines of real changes** — most of the 65k was the 47 broken test files that got generated by the cron but never validated. The actual prod code changes in `a1465cd` are 1500 lines. The original "65k" number was the diff of the whole branch against master, not of any one commit.

2. **Many of the branch's "P3-P5" features already exist on master** — the storage layer, the LLM client, the orchestrator, the api routes all have substantial implementations on master. The branch's version is more elaborate but the fundamental work is done. The 28 new endpoints and 50+ new generators are additive features, not replacements for missing code.

3. **Quality gates needed only docstring updates** — the actual logic of `gate_t1.py` and `gate_t2.py` on master is already correct. The branch's "improvements" are mostly comment-style upgrades that don't change behavior.

4. **The dual-agent cron is fundamentally broken** — it can only do file work, not push, not test against real services. The right path forward is to retire it and do real development in focused sessions like this one. The cron job is still scheduled but the strict prompt makes it silent-exit on every tick.

5. **Cherry-picking the tangled commits doesn't work** — `a1465cd` had 8 files in conflict because master has its own (smaller) versions of the same files. The right approach was to re-implement the valuable parts from scratch (PR 4a: SecurityHeadersMiddleware). This produces cleaner code than the original branch.

---

## Next session recommendations

1. **Check the cron's first few runs under the new prompt.** Verify the silent-exit path is still silent (no DIAG doc spam), and if any cron tick has shell available, look at `docs/DUAL_AGENT_RUN_latest.md` for what it produced. Branches will appear under `cron/<timestamp>` prefixes — review them, cherry-pick the good ones to master.

2. **Delete the branch** once you've verified master at 76d0106 looks good. The branch is drained.

2. **Decide on the cron job** — DONE in this session. New prompt is in place (silent-exit on shell block, real work when shell works, ≤200 lines, branch-prefix `cron/<timestamp>`). Old duplicate "Decide on cron" recommendation superseded.

3. **Pick the next thing from the branch** if you want more value. The top candidates, ranked by value-to-effort:

   | Work | Value | Effort | Notes |
   |------|-------|--------|-------|
   | `orchestrator/feature_flags.py` (567 lines) | High — gates future rollouts | Medium | Required by 28 new endpoints |
   | Manual triage of the 48 unverified test files | Medium | 2-3 hours | Find the 5-10% that are actually valuable |
   | The 28 new API endpoints (cherry-pick + adapt) | High | 1-2 sessions | Each is ~50-200 lines |
   | The 50+ new generator files | High | 1-2 sessions | Each is 500-1500 lines of self-contained code |

4. **Update AGENTS.md** to reflect the new state. The "Recent commits" section shows pre-PR-1 state, the "Active project" section says "Phases 0-4 complete" but the work extracted in this session is P3 (Discord bot hardening). The doc needs a session.

---

## Verification commands (run before deleting the branch)

```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG
git checkout master
git log --oneline 01335d0..HEAD  # 7 commits, all extracted work
PYTHONPATH=. python -m pytest sdv-mod-generator/tests/ -q  # should be 312/312
git diff origin/master..HEAD --stat  # should show only the 7 commits' worth
```

If those check out, the branch can be safely deleted.
