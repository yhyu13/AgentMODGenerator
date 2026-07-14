# Subagent Pipeline Learnings

Session 2026-07-12 → 2026-07-14 — building the AMG 5-agent cron pipeline.

This document summarizes the **lessons learned** from raising 5
subagents (planner+impl, reviewer, test-writer, test-verifier, project-critic)
via a 15-min cron. It's written for humans; the **machine-readable
version** is at `~/.hermes/skills/devops/multi-agent-subagent-pitfalls/`
(auto-loads for future multi-agent cron work).

## TL;DR — The 3 Rules

1. **File-only subagents can't see their own bugs.** Split the work into
   N agents with separate perspectives. Each one reads what the
   previous one wrote. Agent #4 (test verifier) is the safety net —
   always runs after Agent #3, even if Agent #2 was skipped.

2. **The state machine must check ALL unfinished cycles, not just
   the LATEST marker.** A recent v<N> with review=FIX and no tests
   is unfinished — but the LATEST audit+project look "complete" can
   mask it. Add `has_unfinished_fix_cycle()` to detect this.

3. **Parent-side verification is mandatory.** The cron's "file-only"
   mode means Agent #4's audit is a code review, not a pytest run.
   After every cycle, parent must `git pull`, run `pytest`, and E2E
   test the change. The cron is a worker; you are the verifier.

## What Worked

- **State file + state machine dispatch.** Each agent reads the
  previous agent's PENDING_* markers and writes its own. The
  dispatcher scans the markers and routes. Clean, observable,
  recoverable. Better than passing shared state in agent prompts.
- **Per-agent skill files.** Each agent has its own SKILL.md in
  `~/.hermes/skills/`. Cron loads via `skill_view`. Patches to one
  agent don't affect the others.
- **5-pattern test audit.** Agent #4 catches the 5 known-broken-test
  patterns: patch propagation, test-bug-in-itself, source-incomplete,
  missing-_isolate_test_env, AsyncMock-on-sync. Catches 80%+ of
  broken tests.
- **Manifest.json helper.** Shared `generators/core/manifest.py`
  module ensures every 2-gen pack emits a Content Patcher-compliant
  manifest. Avoids per-pack drift.

## What Didn't Work (and the fix)

### 1. Parent-written state silently overwritten
**Symptom:** Parent wrote `PENDING_PICK.md` with the actual next
task. Cron bootstrap-created its own PICK from the 6-day-stale
schedule, picking the wrong work.
**Fix:** Dispatcher's state scan MUST include PENDING_PICK.md in
the list of files to read. Agent #1 must use the parent-written pick
if it exists, only bootstrap as fallback.

### 2. `skip_review=yes` skipped Agent #4 too
**Symptom:** A test file with `skip_review=yes` was committed without
Agent #2 review. Agent #4 was also skipped (it runs after #3 in the
chain). The broken test hung pytest.
**Fix:** Restrict `skip_review: yes` to non-test changes. The chain
becomes `commit+skip_review=yes → Agent #3 → tests exist → Agent #4`
(safety net preserved).

### 3. Agent #2 didn't organically re-audit
**Symptom:** Agent #2 returned FIX citing missing source bundle.
Parent staged it. Agent #1 wrote the impl. But on-disk review still
showed FIX. Cron re-routed to Agent #1 forever — Agent #2 never
re-audited.
**Fix:** Parent-side: after staging a prerequisite, manually
overwrite the review with KEEP if it's still FIX. Agent #2-side:
when writing FIX for a parent-resolvable blocker, mark
`parent-action: <what to do>` and watch for the next commit to
re-audit.

### 4. Rule 6 (new cycle) masked Rule 4 (unfinished FIX)
**Symptom:** v183 review legitimately returned FIX. Latest audit
(v181) was KEEP. Dispatcher fired Rule 6, routing to "new cycle."
Agent #1 re-emitted the same status note 20 times.
**Fix:** Add `has_unfinished_fix_cycle()` BEFORE Rule 6 that scans
all `PENDING_REVIEW_v*.md` for verdict=FIX + no matching tests.
Returns the latest v<N>. Agent #1 receives `unfinished_cycle=<vN>`
and produces a corrected plan+commit.

### 5. AGENTS.md false-missing
**Symptom:** Agent #5 (project critic) reported "AGENTS.md missing
from project root" — but the file was at the parent of the workdir.
The cron's `workdir` is `sdv-mod-generator/`, AGENTS.md is at
`/home/hangyu5/Documents/Gitrepo-My/AMG/AGENTS.md`.
**Fix:** When checking repo-root files, search at BOTH the workdir
and the parent. Only report "missing" after both fail.

### 6. Agent #3 silently skipped
**Symptom:** v175-v177 plans+reviews landed but no tests were
written. Cron silently moved on. The next "RECOVERY" rounds (v178-v180)
finally wrote the tests.
**Fix:** Parent-side check: if a v<N> has review=KEEP but no
`PENDING_TESTS_v<N>.md` after 2-3 ticks, Agent #3 may have been
skipped. Investigate manually or write the test marker directly.

### 7. Manifest.json missing in 2-gen packs
**Symptom:** Generated zips contained `content.json` + assets but no
`manifest.json`. Content Patcher silently rejected them.
**Fix:** Write a shared helper `build_manifest_dict()`. Patch every
2-gen pack's ContentJsonGenerator to also emit manifest.json. Add
unit tests. The helper is the canonical pattern for future ports.

## The Subagent Pipeline Skill (auto-loads for future work)

The machine-readable version of these lessons is at:

```
~/.hermes/skills/devops/multi-agent-subagent-pitfalls/
```

This skill auto-loads when any future multi-agent cron work is
detected. It contains 12 detailed pitfalls with: symptom, root
cause, fix (dispatcher-side + agent-side + parent-side), and the
specific incident that revealed each one.

The companion skill at `~/.hermes/skills/devops/cron-pipeline-state-machine/`
covers the **dispatcher pattern** itself (the architecture). This new
skill covers the **bugs and edge cases** that surface AFTER the
pattern is working.

## Pipeline Architecture (TL;DR)

```
┌─ CRON TICK (every 15m) ────────────────────────────────┐
│ 1. Probe shell (expect BLOCKED)                       │
│ 2. Discover state: scan docs/ for PENDING_*_v*.md     │
│ 3. Run has_unfinished_fix_cycle() check               │
│ 4. Apply state machine → pick agent                   │
│ 5. Load agent prompt via skill_view                   │
│ 6. Execute agent's instructions in this session       │
│ 7. Agent writes its PENDING_* marker                  │
└──────────────────────────────────────────────────────┘
```

State machine rules (priority order):

1. **has_unfinished_fix_cycle()** (NEW 2026-07-14) — overrides Rule 6
   when any recent cycle has verdict=FIX with no tests
2. Rule 1: audit but no project → Agent #5 (critic)
3. Rule 2: no plan, no commit → Agent #1 (planner+impl) [with pick or bootstrap]
4. Rule 3: commit but no review → Agent #2 (or Agent #3 if skip_review=yes)
5. Rule 4: review but no tests → Agent #3 (KEEP) or Agent #1 (FIX/DELETE)
6. Rule 5: tests but no audit → Agent #4 (always runs, safety net)
7. Rule 6: audit + project → Agent #1 (new cycle)

Each rule checks "is the LATEST of marker type X present." The
`has_unfinished_fix_cycle()` check BEFORE Rule 6 ensures the
LATEST-of-each check doesn't mask unfinished cycles.

## Agent Roles

| # | Role | Job | Skips if... |
|---|------|-----|------------|
| 1 | planner+impl | Plan + implement (≤200 lines/tick) | n/a |
| 2 | reviewer | 8-check fidelity audit | skip_review=yes (only non-test changes) |
| 3 | test-writer | Add tests for the impl | never (always runs after KEEP) |
| 4 | test-verifier | 5-pattern audit of tests | never (always runs after tests) |
| 5 | project-critic | Whole-project drift audit | never (every cycle close) |

## File Conventions

- `docs/PENDING_PLAN_v<N>.md` — Agent #1's plan
- `docs/PENDING_COMMIT_v<N>.md` — Agent #1's commit marker
- `docs/PENDING_REVIEW_v<N>.md` — Agent #2's review (verdict: KEEP/FIX/DELETE)
- `docs/PENDING_TESTS_v<N>.md` — Agent #3's test summary
- `docs/PENDING_TEST_AUDIT_v<N>.md` — Agent #4's audit (ALL_KEEP/SOME_*/MAJOR_DELETE)
- `docs/PENDING_PROJECT_AUDIT.md` — Agent #5's project audit (single file, not versioned)
- `docs/PENDING_PICK.md` — parent-written task queue (authoritative)
- `docs/PENDING_SOURCE_BUNDLE.md` — Agent #1's "waiting for parent" notes

## Parent-Side Workflow

After every cron cycle (every 15m), parent should:
1. `git pull` (cron doesn't push)
2. `git status` to see untracked PENDING markers
3. Read `docs/PENDING_PROJECT_AUDIT.md` for Agent #5's summary
4. Spot-check 1 cycle: read PLAN + COMMIT + REVIEW + TESTS + AUDIT
5. Run `pytest <new_test_file> -v` if a new test file landed
6. E2E test: `curl -X POST /v1/mods/generate` and verify the zip

If a v<N> has been stuck (no new marker for 3+ ticks), pause the
cron, investigate, possibly write a follow-up marker.

## When to Build This Pattern

Build a multi-agent subagent pipeline when:
- The work is too large for one agent to do well (single-agent
  perspective-bias on 1000+ line diffs is a known failure mode)
- The tasks are decomposable into discrete cycles (plan → review
  → test → audit → close)
- A file-only mode is acceptable (terminal access is not required
  to make progress, just to verify)
- The work can be split across cron ticks (≤200 line diffs/tick)
- The parent can verify externally (pytest, E2E, git history)

DON'T build this pattern when:
- The work is small (<100 lines total) — one agent is faster
- Real-time feedback is required (cron is 15-min granularity)
- The work is tightly coupled (one big change, not many small
  decomposable cycles)
- A single human can do it in less than the time to set up the
  pipeline (rule of thumb: <1 day of human effort → not worth it)

## Related Documents

- `~/.hermes/skills/devops/multi-agent-subagent-pitfalls/SKILL.md` —
  detailed pitfalls (this doc's machine-readable version)
- `~/.hermes/skills/devops/cron-pipeline-state-machine/SKILL.md` —
  the dispatcher pattern architecture
- `~/.hermes/skills/amg-pipeline-orchestrator/SKILL.md` — concrete
  AMG implementation
- `~/.hermes/skills/amg-agent-{1..5}-*/SKILL.md` — the 5 agent roles
- `~/.hermes/skills/dual-agent-cron-diagnosis/SKILL.md` — diagnostic
  patterns for failing crons
- `docs/CRON_RUN_ARCHIVE_2026-07-{03..06}.md` — historical cron
  output (legacy dual-agent)
- `docs/STARDEW_VALLEY_MOD_STANDARDS.md` — the Content Patcher
  standards that drive the test audits