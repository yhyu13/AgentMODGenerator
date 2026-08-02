# SDV Mod Generator — Closest Path to a Finished MVP

**Date:** 2026-08-01
**Grounding:** verified against the real reference mod at `.reference_mods/TV Shopping Network/`
and the three dependency DLLs in `.reference_mods/deps/` (ContentPatcher, BroadcastAPI, Esca.EMP).

---

## 1. Current state (verified from source)

- Core pipeline is shipped: Route -> Generate (10 feature packs) -> T1 schema gate -> T2 3-judge
  LLM panel -> Package -> S3 / Postgres / Redis. FastAPI + gateway bot + HTTP webhook exist.
- All planned phases in `PHASES.md` are marked done.
- **Local test env is broken** in this checkout: no `.venv`, no `pytest` installed. CI greenness
  unconfirmed.
- `docs/PENDING_PICK.md` shows the cron pipeline still wants to port more feature packs
  (tool_definition, hat_collection) — that is breadth, not depth.

---

## 2. The real MVP blocker

The product promise is: **"user says one sentence -> gets a working zip that loads in the game."**

The bottleneck is **not** generator breadth. It is that the **quality gate does not model the real
target format**. Specifically:

- The reference mod's `content.json` is Content Patcher **2.x object format**:
  ```json
  {
    "Format": "2.9.0",
    "ConfigSchema": { ... },
    "DynamicTokens": [ ... ],
    "Changes": [ { "Action": "Load", ... } ]
  }
  ```
- The current validator (`tests/smapi_validate.py:52`, `validate_content_json`) hard-rejects
  anything that is not a **bare JSON array**:
  ```python
  if not isinstance(content_data, list):
      errors.append("content.json: must be a JSON array, got ...")
  ```
- **Conclusion: your own MVP bar (the reference mod) would FAIL your own gate.** The validator
  encodes an outdated mental model of CP, not the real format.

---

## 3. What a finished MVP output must contain (from the reference)

Real-world output has four layers the current validator/generators likely do not model:

1. **CP 2.x object root** — `Format` / `ConfigSchema` / `DynamicTokens` / `Changes` (not a bare array).
2. **DynamicTokens** — `{{Random:...|key=...}}` and `{{Esca.EMP/PlayerStat: ...}}` for stateful,
   weighted item selection.
3. **Esca.EMP GameStateQuery / PlayerStat conditions** wired into `When` blocks — this is what makes
   the channel stateful and functional (weekly rotating stock, realism-mode junk/refund).
4. **Dependency-aware manifest** — `ContentPackFor` + `Dependencies: [Astraios.BroadcastAPI, Esca.EMP]`
   plus 100 item PNGs in `Assets/Items/` and i18n (`i18n/default.json`) driving item names.

All three dependency DLLs are already vendored in `.reference_mods/deps/`, so real SMAPI-style
validation is feasible locally, not just static string checks.

---

## 4. Reframed closest path to MVP (priority order)

### A. Fix the gate to accept the real format (highest leverage)
- Rewrite `validate_content_json` / `validate_zip_contents` to accept CP 2.x object roots.
- Validate `ConfigSchema`, `DynamicTokens`, `Changes`, dependency declarations, and
  token-referenced asset paths.
- Until this is right, "finished" is undefined.

### B. Add a golden test: the reference mod must pass
- Build a fixture from `TV Shopping Network/` (manifest + content.json + i18n) that the validator
  accepts. Pins the validator to reality and prevents regressions.

### C. Diff generated shop_channel output against the reference
- Generate a TV-shopping-channel mod and structurally compare to the real one:
  DynamicTokens, EMP conditions, i18n, asset inventory. Fix gaps.

### D. Real SMAPI validation via `deps/` DLLs
- Move from static checks to dependency-aware validation once generators emit the correct shape.

### E. Delivery UX (post-format work)
- `on_message` free-form prompt -> `run_pipeline_background` (`bot.py:112` only greets today).
- Completion-push notifier (Redis watcher DMs the zip on done/failure).
- Real Ed25519 webhook signature (`webhook.py:18` is a stub).
- Thin web UI (single POST form + poll + download) reusing existing `/v1/mods/generate` + `/status`.

### F. Stop for now
- Porting more feature packs (tool_definition, hat_collection) until A-D ship. Each new pack adds
  untested surface without moving the MVP goalpost.

---

## 5. Definition of Done for this MVP

- User describes a mod in Discord chat (or via form) -> receives a zip.
- The zip passes a CP-2.x-aware gate (with the reference mod as a passing golden test).
- The zip installs cleanly and loads (dependency-aware validation using the vendored DLLs).
- Delivery happens without manual polling (completion push).

---

## 6. Concrete next actions (doable now)

- **Action A:** Rewrite `tests/smapi_validate.py` for CP 2.x + add reference-mod golden fixture.
- **Action B:** Add a generator-vs-reference structural diff check for the TV shopping channel.
- **Action C:** Fix local test env (`make install`) and confirm suite green.

---

## 7. Files of interest

| Path | Note |
|---|---|
| `tests/smapi_validate.py` | Validator that rejects the real CP 2.x format |
| `tests/test_smapi_validate.py` | Tests that encode the wrong (array-only) model |
| `.reference_mods/TV Shopping Network/content.json` | The real MVP bar (1024 lines) |
| `.reference_mods/TV Shopping Network/manifest.json` | 3-dependency manifest example |
| `.reference_mods/deps/` | ContentPatcher + BroadcastAPI + Esca.EMP DLLs |
| `app/discord/bot.py:112` | `on_message` only greets; no pipeline wiring |
| `app/discord/webhook.py:18` | Ed25519 verify is a stub |
| `docs/PENDING_PICK.md` | Cron queue still pushing breadth (tool/hat) |
