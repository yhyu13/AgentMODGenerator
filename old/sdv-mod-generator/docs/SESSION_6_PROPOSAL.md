# Session 6 Proposal — First batch of new feature generators

**Status:** proposal (cron tick v87, 2026-07-05, no parent shell required)
**Author:** Dual-Agent Orchestrator (cron, file-only mode)
**Audience:** parent session (when the user returns)

---

## Why now

After Sessions 1-5 (v71 schedule update), master has all 26 production
endpoints live and the test-coverage sweep (v68-v86) closed every
single endpoint that doesn't depend on the missing `app/estimation.py`
restore. The two remaining handler-side gaps are:

1. `get_phase_detail` (routes.py:762) — covered transitively by v86's
   `test_phase_detail_response_schema.py` schema test, but no full
   handler test (blocked because the handler imports
   `app.estimation._DEFAULT_SECONDS` and `estimate_seconds_for_phase`
   inside its body, so calling it raises `ImportError` until parent
   restores the module).
2. `_get_cancellation_reason_safe` (routes.py:2298) — internal helper,
   covered transitively by `test_summary_endpoint.py` via
   `get_mod_summary`. Not worth a dedicated test.

Both gaps collapse into one restore: **`app/estimation.py`** (still
pending per `docs/PENDING_SOURCE_BUNDLE.md`). Until that lands, no
more test-coverage work is unblocked.

Meanwhile, the P3-P5 schedule's **Session 6** is wide open: 50+ new
feature generators in the `discord-ops-hardening` branch are not on
master. Porting the first batch is the highest-leverage remaining
work, and it requires no shell access from the cron (the parent
stages source bundles, the cron ports).

This document is the v87 round's deliverable: a concrete
**scope-and-order proposal** for that work, written so the parent
can pick a starting generator and run the stage command without
further research.

---

## What "porting a generator" means (concrete checklist)

Per the v22 cron archive's caveat and the schedule's Session 6
description, a generator port is a **three-file change**, not just
the generator file:

| File | What changes | Why |
|------|-------------|-----|
| `generators/packs/stardew_valley/features/<name>/__init__.py` | the generator class(es) — usually 1-3 generator classes per directory | the actual work |
| `generators/packs/stardew_valley/features/__init__.py` | add the new generator classes to the imports + `__all__` list | so downstream `stardew_valley/__init__.py` can find them |
| `generators/packs/stardew_valley/__init__.py` | add the new generator imports + extend `supported_phases` list in `_MANIFEST` + add a `PHASE_GENERATORS[<phase>] = PhaseGenerators(...)` entry | so the pack's `list_phases()` and `get_generators(phase)` know the new phase exists |
| `orchestrator/router.py` | add a `if phase == "<phase>":` arm to `_default_generators_for_phase` (defence-in-depth fallback) AND optionally add keyword→phase entries to `_PHASE_BY_KEYWORD["stardew_valley"]` | so the router can route a prompt to the new phase AND so the fallback path doesn't emit `router.default_generators.unknown` WARNING |

**Phase registration is mandatory.** Skipping it would create the
"override fires but orchestrator crashes looking for a non-existent
generator" failure mode the v22 archive flagged. The cron archive's
PENDING_COMMIT_v22 specifically warned:

> Registering the phase without the generator would create a
> broken state where the override fires but the orchestrator
> crashes looking for a non-existent generator.

That's why the proposal below **always pairs a generator port with
its three sibling edits**, and why the recommended first batch is
the set of phases that the branch already has wired up end-to-end.

---

## Recommended first batch (5 generators)

Picked for: smallest size, clearest domain boundaries, least
external coupling, and most obvious user-visible value. Each
entry: estimated source lines + the phase it gates + why it's
a good first pick.

### 1. `weather_event` — 1 generator, ~300 lines source

The router's `weather_event` priority (router.py:148-151) already
**routes** prompts to this phase but the phase is **not registered**
in `StardewValleyPack.supported_phases`, so the pack returns no
generators and the orchestrator falls through to `_default_generators_for_phase`
which has no `if phase == "weather_event"` arm. Result: the
WARNING `router.default_generators.unknown` fires for every
weather prompt. This is the single biggest unblocked gap.

The branch has a single `WeatherEventGenerator` class. Port is
self-contained. After the port, the v27 router priority override
becomes load-bearing — every "rain storm event" prompt actually
generates files instead of falling through.

**Why first:** fixes the only phase the router already routes to
but cannot service. Tests for the router priority exist
(`tests/test_router_weather_priority.py`); a follow-up round can
add an end-to-end "rain prompt generates a weather_event zip" test.

### 2. `achievements` — 1 generator, ~250 lines source

Steam achievement equivalents for SDV. Self-contained, no game-data
dependencies beyond the manifest generator's output, single class.
New phase id: `achievements`. New router keywords: `achievement`,
`badge`, `trophy`, `steam achievement`.

**Why second:** small, isolated, no shared dependencies with the
existing phases. A clean test of the three-file port pattern
without any cross-generator coupling.

### 3. `weapon_definition` — 2 generators, ~700 lines source

Adds `weapon_definition_generator` + `weapon_content_json_generator`.
Phase id: `weapons`. Router keywords: `weapon`, `sword`, `tool`,
`damage`, `attack`.

**Why third:** the two-generator pattern (one logic + one content
JSON) matches `npc_schedule`/`event_mod`/`farm_expansion`'s
established shape, so the pack-registration code is a known
quantity. Doubles as a template for future two-generator phases.

### 4. `tv_schedule` — 1 generator, ~400 lines source

The branch's `tv_schedule_generator` extends the existing
`shop_channel` phase (TV channel content) with show scheduling
metadata. Phase id: `tv_schedule`. Router keywords: `tv schedule`,
`show`, `broadcast schedule`, `episode`.

**Why fourth:** extends an existing phase rather than standing
alone — exercises the "generator joins an existing phase"
code path in `StardewValleyPack.get_generators`. After this
port, the `shop_channel` phase has 12 generators instead of 11.

### 5. `fishing_overhaul` — 1 generator, ~1197 lines source

The biggest of the small. Phase id: `fishing`. Router keywords:
`fishing`, `fish`, `catch`, `rod`, `bait`, `fish pond`,
`fishpond`. Single class, but with 1197 lines it's at the upper
edge of the cron's 200-line cap. **Recommend porting as 2-3 cron
rounds** (e.g. v88 imports + class skeleton, v89 the prompt
templates + Pydantic models, v90 the `generate()` body).

**Why fifth (and split into multiple rounds):** it's the first
generator that the cron must split across rounds. Establishes
the pattern that future >800-line generators will need.

---

## What is NOT in this first batch

To keep round count down, deliberately exclude:

- **`witch_swamp`, `animal_expansion`, `witch_warp`** — these have
  cross-phase dependencies (e.g. witch_swarp references
  farm_expansion's `MapEditGenerator` outputs). Porting them
  without their dependencies creates the v22 broken-state hazard.
  Defer to Session 7.
- **Minecraft / Skyrim generators** — the branch has generator
  packs for other games too, but master's `_GAME_KEYWORDS` and
  `_PHASE_BY_KEYWORD` only cover Stardew Valley fully. Cross-game
  ports need `generators/packs/minecraft/__init__.py` and the
  matching skyrim pack, which don't exist on master. Defer to
  Session 8.
- **Generators with custom ContentPatcher schemas** — anything
  that introduces a new JSON shape (e.g. custom TV channel
  format) needs a gate_t1 update. Defer until Session 9.

---

## Cron-friendly round breakdown

| Round | Files | Lines | Source bundle needed |
|-------|-------|-------|----------------------|
| v88 | `weather_event/__init__.py` + 3 sibling edits | ~330 | `_source_weather_event.py.txt` |
| v89 | tests for v88 (1 schema + 4 handler) | ~180 | none (existing fixtures) |
| v90 | `achievements/__init__.py` + 3 sibling edits | ~280 | `_source_achievements.py.txt` |
| v91 | tests for v90 | ~150 | none |
| v92 | `weapon_definition/__init__.py` (2 classes) + 3 sibling edits | ~750 | `_source_weapon_definition.py.txt` |
| v93 | tests for v92 | ~200 | none |
| v94 | `tv_schedule/__init__.py` + 3 sibling edits (extends existing shop_channel phase) | ~450 | `_source_tv_schedule.py.txt` |
| v95 | tests for v94 | ~170 | none |
| v96 | `fishing_overhaul/__init__.py` part 1 (imports + class skeleton + Pydantic models) | ~400 | `_source_fishing_overhaul.py.txt` |
| v97 | `fishing_overhaul/__init__.py` part 2 (generate() body) + sibling edits | ~850 | (same bundle as v96) |
| v98 | tests for v96+v97 | ~250 | none |

**11 rounds total** (5 generators × ~2 rounds + 1 generator split
into 3 rounds = 11). At ~3 cron ticks per day that's ~4 days of
file-only work, fully decoupled from `app/estimation.py` restore.

---

## One open question for the parent

The cron archive's PENDING_COMMIT_v22 explicitly warned that
**generator + phase registration must land atomically**. The
round breakdown above splits each generator across 2-3 rounds
(generator + tests), but **never splits generator from
registration**: each generator's first round includes all 3
sibling edits. This means v88's tests can pass with the
generator registered but without the new tests, and v89's tests
can fail-to-pass cleanly. The atomicity hazard is mitigated.

If the parent wants stricter atomicity (every generator lands
in a single round with all 3 edits + tests), the schedule
extends to ~16 rounds because each of v92, v94, v96/v97
exceeds the 200-line cap if bundled with tests. The cron
recommends the split-with-atomic-pairs approach above.

---

## Next action for the parent (when the user returns)

1. Stage source bundle for v88 first:
   ```bash
   cd /home/hangyu5/Documents/Gitrepo-My/AMG
   git show discord-ops-hardening:sdv-mod-generator/generators/packs/stardew_valley/features/weather_event/__init__.py \
     > sdv-mod-generator/docs/_source_weather_event.py.txt
   git add sdv-mod-generator/docs/_source_weather_event.py.txt
   git commit -m "chore(docs): pre-stage source bundle for Session 6 v88 (weather_event generator)"
   git push origin master
   ```
2. Resume cron: `cronjob action=resume job_id=8faa6346fe1e`
3. v88 picks up: reads the bundle, ports the 4 files (generator +
   3 sibling edits), writes PENDING_COMMIT_v88.md.

Or, if Session 2's `app/estimation.py` restore is higher priority,
stage that bundle instead (see `docs/PENDING_SOURCE_BUNDLE.md` for
the one-shot restore recipe). v87's option (a) — restore
`app/estimation.py` then close the `get_phase_detail` test gap —
is also still on the table.