# MVP Plan Comparison — Final Verdict & Execution Plan

**Date:** 2026-08-01
**Source docs compared:** `doc/claude/`, `doc/deepseek/`, `doc/qwen/`, `doc/minimax/`
**Verdict:** `doc/claude/mvp-finish-roadmap-2026-08.md` is the best plan for shortest path to most valuable MVP.

---

## 1. Comparison

| Doc | Grounding | MVP bar | Effort | Shortest path? | Valuable? |
|---|---|---|---|---|---|
| **claude** | 5 parallel code-audit agents, line-number evidence, commit pinned | "happy path → working, loadable zip; no stubs; observable" | 6-week full roadmap, but the MVP floor (Week 1 + Monday PR) is hours-to-days of S-effort fixes | ✅ | ✅✅ |
| **deepseek** | Verified against `.reference_mods/` | "generated mod matches CP 2.x object format" | Hours (validator rewrite + golden test) | ✅ but narrow | ✅ single highest-leverage fix |
| **qwen** | Shallow; no code audit | "type prompt in Discord → get zip" | ~3 hours | ✅ shortest | ❌ built on false premise ("infra is solid" — it isn't) |
| **minimax** | Verified against reference mod | Full TVSN parity | ~7.5 days rework of `shop_channel` | ❌ longest | ✅ but over-scoped for an MVP |

## 2. Why claude wins

1. **Only doc that found the dominant failure pattern:** 7 of 10 phases emit unloadable zips
   (no manifest), cancellation is fake (`cancel_mod` never calls `task.cancel()`),
   `MAX_T2_ITERATIONS=0` means T2 never loops, and `shop_channel`'s `content.json` is a bare
   list the game rejects. qwen's plan ignores all of this — following it ships a bot that
   generates broken mods.
2. **The valuable MVP is short, not 6 weeks:** the correctness floor is almost all 1–2 line
   patches + one templating win (shared `ManifestGenerator`), plus the "Monday morning PR" of
   5 changes. Working product in ~a few days.
3. **Two independent audits converge on the same root cause:** claude's bombshell #3
   (`gate_t1.py` *enforces* the wrong list shape) is the code-level confirmation of deepseek's
   thesis (the gate encodes an outdated CP mental model — the reference mod itself would fail
   its own validator). Convergence = high confidence.

## 3. Executed plan (shortest path to valuable MVP)

### Phase 0 — Validator matches reality (deepseek)
- Rewrite `tests/smapi_validate.py` to accept CP 2.x object roots.
- Golden test: the reference mod (`.reference_mods/TV Shopping Network/`) must pass.

### Phase 1 — Correctness floor (claude Week 1 + Monday PR)
- Fix `shop_channel` `content.json` shape (dict with `Format`/`Changes`).
- Tighten T1 (`gate_t1.py`) to enforce the correct shape.
- Shared `ManifestGenerator` for the 7 manifestless phases.
- Real cancellation via `request_id → asyncio.Task` registry.
- `MAX_T2_ITERATIONS` default 0 → 1.
- Wire `t2_three_judge_panel` flag.
- Replace bare `except: pass` with `logger.warning`.
- `Depends(verify_api_key)` on unauthenticated routes.

### Phase 2 — UX intake (qwen + claude Week 5)
- Free-form `on_message` → pipeline (with greeting/short-message guards).

### Phase 3 — Verify
- `make test` full suite green, `make lint` clean.

## 4. Deferred (explicitly out of this MVP run)

- Minimax's full TVSN parity rework (Option A, 7.5 days) — only if reference parity is the
  launch goal.
- Infra substrate (Alembic, CI, Docker hardening), observability, testcontainers — claude
  Weeks 2–4.
- Knowledge base seeding — claude Week 6.
