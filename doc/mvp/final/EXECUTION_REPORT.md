# MVP Execution Report — 2026-08-02

**Plan executed:** `doc/mvp/final/FINAL_COMPARISON.md` (claude roadmap Week 1
+ Monday PR + deepseek validator + qwen intake)
**Result:** `make test` equivalent — **1152 passed, 12 skipped** (skips are
bash-only scripts on Windows), ruff clean on all new code.

---

## 1. Phase 0 — Validator matches reality (deepseek)

- `tests/smapi_validate.py` — accepts CP 2.x object root (`Format` +
  `Changes`) alongside the legacy array root; validates `Changes` actions,
  resolves `@/`-prefixed and tokenized `FromFile` paths; parses SMAPI-style
  JSONC (comments + trailing commas) like the real reference mod.
- `tests/test_reference_mod_validation.py` — golden test: the actual
  `.reference_mods/TV Shopping Network/` mod (manifest + content.json +
  full zip) must pass the validator. Previously the validator's array-only
  model would have FAILED the product's own MVP bar.

## 2. Phase 1 — Correctness floor (claude Week 1)

- **shop_channel content.json shape (§1.3):** `ContentJsonGenerator` now
  emits `{"Format": "1.29.0", "Changes": [...]}` instead of a bare list;
  `validate_output` enforces the object root.
- **T1 gate (§1.3):** `quality/gate_t1.py` `content_json_generator` branch
  enforces the CP 2.x shape (dict + Format + Changes) while tolerating the
  legacy list root.
- **Manifest for the manifestless phases (§1.1):** new shared
  `generators/packs/stardew_valley/manifest_generator.py` registered in
  texture, event_mod, custom_crafting, achievements, npc_schedule,
  farm_expansion. All 10 phases now produce a manifest when run standalone.
- **texture missing asset (§1.2):** `TextureGenerator` emits the referenced
  `assets/custom_sprite.png` placeholder.
- **Real cancellation (§1.5/§3.4):** `request_id → asyncio.Task` registry in
  `orchestrator/pipeline.py` (`run_pipeline_background` registers,
  `cancel_pipeline_task` cancels); `POST /v1/mods/cancel/{id}` and Discord
  `/cancel` actually stop the pipeline; the coroutine persists `cancelled`
  status so the notifier never DMs "done" for a cancelled request.
- **MAX_T2_ITERATIONS default 0 → 1 (§1.9):** `app/config.py` — T2 retry is
  a real loop now; still bounded 0–2 by `validate_config`.
- **t2_three_judge_panel flag wired (§1.4):** `quality/gate_t2.py` reads the
  flag (off = single technical-compliance judge); quorum adapts to panel
  size.
- **Swallowed exceptions → visible (§1.8/§2.4–2.6):** `gate_t2.py` score
  fallback, `_log_hook.py` (both variants), `llm/client.py` structured-output
  fallback now log `logger.warning` with error type.
- **Auth on 10 routes (§3.7/§3.12/§3.13):** `Depends(verify_api_key)` on all
  8 `/v1/feature_flags*` routes + `/v1/mods/{id}/t2_judges` + `/logs`.

## 3. Phase 2 — UX intake (qwen + claude Week 5)

- **Free-form `on_message` (qwen #1):** any non-trivial chat message
  (≥20 chars, not a command/greeting) triggers the pipeline via the same
  background + DM-notifier path as `/generate`. Intake rules extracted into
  `_extract_prompt_from_message` for unit testing.

## 4. Latent bugs found & fixed while testing

- **`storage/postgres.py` init deadlock:** `get_session_factory()` held a
  non-reentrant `threading.Lock` while calling `get_engine()`, which tries
  to acquire the same lock — the FIRST DB touch hung forever whenever the
  engine hadn't been created yet (this was the actual cause of the
  `test_security_headers` hang, not asyncpg). Fixed with `threading.RLock`;
  also added `connect_args={"timeout": 5}` (bounded connect, prod hardening).
- **`GET /v1/mods` 500 on DB outage:** now returns 503 with a clear message
  (matches the endpoint's documented contract).

## 5. New tests

| File | Covers |
|---|---|
| `test_reference_mod_validation.py` | golden test against the real reference mod |
| `test_phase_manifest_isolation.py` | all 10 phases standalone → loadable zip (the §1.1 regression) |
| `test_shop_channel_shape.py` | content.json object root + T1 shape enforcement |
| `test_cancel_real.py` | task registry actually cancels + persists status |
| `test_on_message_intake.py` | free-form intake rules |
| `test_t2_flag_wiring.py` | 3-judge ↔ single-judge switch + quorum math |

Existing tests updated only where they pinned the pre-fix broken contract
(texture/achievements/custom_crafting generator lists, POSIX-only bash
tests skip on Windows, platform-absolute path in package test).

## 6. Verification

- Full suite: `1152 passed, 12 skipped` (12 = bash-script tests on Windows).
- `test_pipeline_integration.py` (previously excluded from `test-quick`) now
  passes: 15/15.
- Ruff: clean on all new/changed code (repo-wide lint remains non-blocking
  with pre-existing findings; mypy cannot run in this repo layout — the
  hyphenated package dir, pre-existing and non-blocking per Makefile).
