# SDV Mod Generator — MVP Next Steps

**Date**: 2026-08-01
**Context**: Post-Phase 5, all planned phases shipped. This doc identifies the closest path to a "finished" MVP.

---

## Current State Assessment

The project infrastructure is solid:
- FastAPI + LangGraph pipeline with 10 feature phases
- Discord bot (gateway + webhook) with slash commands
- T1/T2 quality gates
- Docker deployment, runbook, metrics
- 100+ test files

The `PHASES.md` "Open Follow-ups" list is **stale** — 3 of 5 items are already implemented:
- ~~Completion-push DM~~ → `notifier.py` exists and is wired
- ~~Ed25519 webhook signature~~ → `webhook.py:verify_signature` now uses PyNaCl (real implementation)
- ~~Free-form `on_message`~~ → partially done (greeting handler exists at line 112)

---

## What "Finished MVP" Means

An MVP is **done** when a real user can type a prompt in Discord and get a working mod zip via DM, with no manual polling.

---

## Gaps Identified (ordered by impact)

### 1. Free-form `on_message` → pipeline (HIGH IMPACT, ~40 lines)

**Problem**: Currently `on_message` only greets on "hi/hello". Users expect to type "make a TV shopping channel" in chat and have it Just Work™ — not `/generate make a TV shopping channel`.

**Location**: `sdv-mod-generator/app/discord/bot.py:on_message` (line 112)

**What to add**:
```python
# After the greeting check, add:
if len(content) > 20:  # heuristic: non-trivial message
    # Run pipeline directly
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    await redis_set_status(request_id, "pending")
    await set_notification_target(request_id, user_id=str(message.author.id), channel_id=message.channel.id)
    run_pipeline_background(request_id, str(message.author.id), message.content)
    await message.channel.send(f"Started! Request ID: `{request_id}` — I'll DM you when it's ready.")
    return
```

**Impact**: Users can chat naturally instead of learning slash commands.

---

### 2. Packager: strip intermediate files (BUG FIX)

**Problem**: `STARDEW_VALLEY_MOD_STANDARDS.md` §8 documents a known bug: intermediate files like `weather_buffs.json` and `weather_dialogue.json` leak into the final zip. T2 judges flag these as "custom JSON files SDV doesn't read."

**Location**: `sdv-mod-generator/generators/packager.py`

**Fix**: Maintain a blocklist of intermediate file patterns and skip them when writing the zip.

**Impact**: Cleaner zips, higher T2 scores, mods that actually work in SMAPI.

---

### 3. LLM provider reliability (OPERATIONAL)

**Problem**: The `.env.example` shows MiniMax as the default OpenAI-compatible provider. If the LLM is down or rate-limited, T2 returns 0 and mods ship unflagged.

**What to add**:
- Retry/backoff at the `get_client()` level (not just `generate_structured`)
- Surface T2=0 more visibly in the DM ("mod shipped but quality check was skipped")

**Location**: `sdv-mod-generator/llm/client.py`, `sdv-mod-generator/app/discord/notifier.py`

**Impact**: Users know when quality check was skipped; fewer silent failures.

---

### 4. Test suite hygiene (LOW IMPACT BUT CLEAN)

**Problem**: 100+ test files, some with overlapping names (`test_feature_flags*.py` has 8 files). The `test_pipeline_integration.py` is skipped in `test-quick` — verify it actually passes.

**What to do**:
- Run `make test` (full suite) and confirm green
- Consider consolidating overlapping test files

**Impact**: Confidence in CI, easier onboarding.

---

### 5. Stale doc cleanup (COSMETIC)

**Problem**: `docs/` has 17 files including `PENDING_REVIEW_*.md`, `CRON_RUN_ARCHIVE_*.md`, `DUAL_AGENT_RUN_*.md`. These are working docs that should either be archived or deleted before a "release."

**What to do**:
- Move `CRON_RUN_ARCHIVE_*.md` to `docs/archive/`
- Delete or consolidate `PENDING_*.md` files
- Keep `RUNBOOK.md`, `STARDEW_VALLEY_MOD_STANDARDS.md`, `ONBOARDING.md`

**Impact**: Repo hygiene, clearer signal for new contributors.

---

## Recommended Next Steps (ordered)

| # | Task | Effort | Impact |
|---|---|---|---|
| 1 | Wire `on_message` → pipeline | ~1 hour | Users can chat naturally |
| 2 | Fix packager intermediate file leak | ~30 min | Cleaner zips, higher T2 scores |
| 3 | Update `PHASES.md` open follow-ups | ~15 min | Remove stale items |
| 4 | Add T2=0 visibility in DM | ~30 min | Users know when quality check was skipped |
| 5 | Clean up `docs/` stale files | ~30 min | Repo hygiene |

Tasks 1-3 can be done in a single session. Task 1 alone makes the bot feel "finished" from a user perspective.

---

## Implementation Notes

### Task 1: Free-form `on_message`

**Files to modify**:
- `sdv-mod-generator/app/discord/bot.py` (line 112)

**Key considerations**:
- Use `len(content) > 20` as a heuristic to avoid triggering on short messages
- Reuse the existing `run_pipeline_background` + `set_notification_target` pattern from `/generate`
- Log the event as `discord.message.pipeline_triggered` for observability

### Task 2: Packager intermediate file strip

**Files to modify**:
- `sdv-mod-generator/generators/packager.py` (line 32, `package()` function)

**Blocklist to add**:
```python
_INTERMEDIATE_FILE_PATTERNS = [
    "weather_buffs.json",
    "weather_dialogue.json",
    "festival_shop_data.json",
    "festival_map_data.json",
    # Add other intermediate patterns as discovered
]
```

**Key considerations**:
- Check against the blocklist before writing each file to the zip
- Log skipped files as `packager.intermediate_file_skipped` for debugging

### Task 3: Update PHASES.md

**Files to modify**:
- `PHASES.md` (line 36-42, "Open Follow-ups" section)

**What to change**:
- Remove the three completed items (completion-push DM, Ed25519 signature, free-form on_message)
- Keep "Test stability" and "Stale doc" as the only open items

---

## Verification Checklist

After implementing tasks 1-3:

- [ ] `make test` passes (full suite)
- [ ] `make lint` passes (mypy + ruff)
- [ ] Manual test: type "make a TV shopping channel" in Discord chat → pipeline runs → DM arrives with zip
- [ ] Manual test: inspect the zip → no `weather_buffs.json` or other intermediate files
- [ ] `PHASES.md` accurately reflects current state

---

## Future Work (post-MVP)

These are **not** blocking the MVP but would improve the product:

- **Multi-turn conversation**: Let users refine mods via follow-up messages ("make the items cheaper", "add more NPC dialogue")
- **Mod preview before shipping**: Show the user a summary of what will be generated before running the full pipeline
- **Analytics dashboard**: Track which phases/generators are used most, T2 score distribution, failure modes
- **SMAPI validation in CI**: Run the generated zip through SMAPI's validator to catch structural issues before delivery
