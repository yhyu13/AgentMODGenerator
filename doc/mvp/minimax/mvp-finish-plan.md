# MVP Finish Plan — SDV Mod Generator

**Date:** 2026-08-01
**Code base:** `sdv-mod-generator/`
**Reference mod:** `C:\Git-repo-my\AgentMODGenerator\.reference_mods\TV Shopping Network\` (Airyn, 1.0.1)
**Goal:** A Discord prompt produces a Content Patcher mod that matches the TV Shopping Network reference end-to-end.

---

## 1. Current State (today)

### 1.1 What's done
- Phase 0–5 of PHASES.md is shipped.
- Orchestrator: Router → 11 generators → T1 gate → T2 3-judge panel → packaging.
- Discord gateway bot + Ed25519 webhook, completion-push DM notifier.
- 10 feature packs (shop_channel, achievements, weather_event, custom_crafting, npc_schedule, event_mod, farm_expansion, texture, tool_definition, weapon_definition).
- 9 follow-ups listed in PHASES.md; cross-checked against code:
  - 8 of 9 are actually DONE (notifier, Ed25519 verify, etc.).
  - 1 follow-up remains: **free-form `on_message` prompt handler** in `app/discord/bot.py:112`.

### 1.2 The real gap (not in PHASES.md)
The reference TV Shopping Network mod is a single, fully-functional Content Patcher mod:

| Component | Reference | Current `shop_channel` pack |
|---|---|---|
| `manifest.json` | 21 lines, deps on BroadcastAPI + Esca.EMP | Manifest written, **no `Dependencies` declared** |
| `content.json` | **1155 lines** with `DynamicTokens` (~100 `TVSNItemID` lookups) + `Changes[]` (Load, EditImage overlays, 4 BroadcastAPI EditData channels, TriggerActions, Data/Mail, Include RealismMode) | **Not produced by any generator** |
| `Assets/Items/item_N.png` | 100 PNGs overlaid via `EditImage ToArea` | 1 placeholder PNG (`shop_sprites.png`) |
| `Assets/TVSNChannelIcon.png`, `TVSNLetterBackground.png`, `Junk.png` | 3 static PNGs | Catalog background + logo PNGs (no JU icon, no letter bg) |
| `Data/RealismMode.json` | ~6 KB conditional CP patch (junk drop, refund quest, SpecialOrders, CraftingRecipes) | **Not produced** |
| `i18n/default.json` | **35 KB** — 100 item names, 100 sales-pitch descriptions, channel text, letter texts, realism-mode strings | **Not produced** |
| `assets/data/shops.tsv` | n/a (TVSN uses DynamicTokens, not shops.tsv) | Produced (orphan output) |
| `assets/data/tv_channels.json` | n/a (TVSN uses BroadcastAPI EditData) | Produced (orphan output) |
| `mail/*.json` | n/a (TVSN uses `Data/Mail` EditData) | Produced (orphan output) |
| `assets/data/damage_modifiers.json` | n/a | Produced (orphan output) |
| `assets/data/catalog_preview.json` | n/a | Produced (orphan output) |

**The current `shop_channel` pack produces a TV-shopping-themed blob of files that does NOT match Content Patcher format.** A real SDV install would load the manifest but the game would see nothing happen. There is no `content.json`, no `DynamicTokens`, no `EditData` patches, no `EditImage` overlays, no `i18n`, no BroadcastAPI channel injection, no TriggerActions, no 100 item icons.

### 1.3 The "MVP" claim is misleading
PHASES.md says "all planned phases shipped." But the **functional bar** — generate a mod equivalent to TV Shopping Network — is not met. The architecture is right, the shapes are wrong.

### 1.4 Uncommitted housekeeping
- `AGENTS.md` and `CLAUDE.md` both have large uncommitted rewrites (273→101 lines, 107→178 lines).
- `README.md` describes the old flat layout (`p0_texture.py`, `p1_shop_channel.py`) — repo moved to `generators/packs/stardew_valley/features/`.
- `tests/test_pipeline_integration.py` is skipped by `make test-quick` — cause unknown.

---

## 2. Three Options (recommendation: Option A)

### Option A — Rewrite `shop_channel` to CP-shape (recommended)
Single worktree `phase6-shop-channel-cp-shape`. Keep the 11 existing generator classes but **re-shape every output to CP `content.json` entries**. Add a 12th generator that composes everything into a single `content.json`. Effort: ~600–1000 LOC of rework, 3–5 days focused work.

### Option B — Ship what's there as "lite MVP"
Acknowledge the current pack does NOT replicate TVSN. Finish the 5 listed follow-ups. Document the limitation. Ship as "MVP 1.0 — themed CP mod generation, full TVSN parity is MVP 2.0."

### Option C — Clone the reference verbatim
Treat the unzipped `TV Shopping Network/` as a **base template**. Generators become parameterized substitutions over the static reference. Less LLM, more deterministic.

**Recommendation: Option A.** It's the only path that honors the explicit "bar MVP 2.0 aims to match" line in PHASES.md. The shortcut: read the reference's `content.json` (1155 lines of source of truth) and let it dictate the generator contract. LLM becomes a substituter, not an author — the 100 item IDs, prices, names, descriptions are the LLM's job; the surrounding CP wiring is deterministic.

---

## 3. Option A — Detailed Plan

### 3.1 Worktree
```bash
git fetch origin && git status
git worktree add ../project-phase6 -b phase6-shop-channel-cp-shape
```
Base: current master (commit `349c16b`).

### 3.2 Phase 0 — Pre-work (clean baseline)
Commits to land BEFORE the rework, so any rework is a clean diff:

1. **Commit `AGENTS.md` + `CLAUDE.md` rewrite** — uncommitted doc refresh, 2 files.
2. **Refresh `README.md`** — match the current pack layout (`generators/packs/stardew_valley/features/<feature>/`), drop the dead `p0_texture.py`/`p1_shop_channel.py` references, update directory tree.
3. **Update `PHASES.md`** — mark the realistic post-launch follow-ups done (notifier, Ed25519, test stability — all complete per code audit). Move the remaining `on_message` work into Phase 6 scope.
4. **Investigate `test_pipeline_integration.py`** — why is it skipped by `make test-quick`? Either re-enable or document.

No code logic changes in Phase 0. Pure housekeeping.

### 3.3 Phase 1 — Define the generator contract (TDD-first)

Define the **target CP output shape** as a JSON Schema fixture. This is the contract every generator must satisfy. File: `sdv-mod-generator/tests/fixtures/tvsn_reference/content.json` (copy of the reference mod's `content.json` minus the 100 DynamicTokens, which become parametric).

Output a single `tests/test_shop_channel_contract.py` that:
- Loads the reference mod's `content.json` (excluding `Assets/`, `i18n/`).
- Runs the pipeline against a representative prompt.
- Asserts the generated `content.json` has the same top-level structure: `Format`, `ConfigSchema`, `DynamicTokens[*]`, `Changes[*]`.
- Asserts each `Change` has the right `Action`, `Target`, `Entries` shape per the reference.

This is the **T1 gate contract** for the shop_channel pack. Until this passes, the rework isn't done.

### 3.4 Phase 2 — `ContentPatchComposer` (the 12th generator)

New file: `sdv-mod-generator/generators/packs/stardew_valley/features/shop_channel/content_composer.py` (or inline if compact).

Class: `ContentPatchComposerGenerator(BaseGenerator)`:
- `name = "content_patch_composer"`
- `phase = "shop_channel"`
- Runs **last** in the pipeline (after image/i18n generators).
- Reads `output.metadata` from prior generators + the output files.
- Emits a single `content.json` with:
  - `Format: "2.9.0"` (matches reference)
  - `ConfigSchema.RealismMode` (matches reference, dropped if `RealismMode=false`)
  - `DynamicTokens` array:
    - `TVSNRandomItem` (random index 1–100)
    - `TVSNItemPrice` (random price from the 100-item price list)
    - `TVSNRandomItem2`, `TVSNRandomItem3` (catalogue siblings)
    - `TVSNCatalogueRandom1/2/3` (mixed real/fake items)
    - `TVSNItemID` (the 100 `When` blocks picking item-ID by `Esca.EMP/PlayerStat: TVShoppingNetworkItem`)
    - `TVSNRefundQuest` (empty unless RealismMode + junk drop)
  - `Changes[]` array:
    - `Load` letter background
    - `Load` channel icon
    - `EditImage` × 4 (channel icon + 3 catalogue overlays)
    - `EditData` BroadcastAPI `CustomChannels` × 4 (TVShoppingNetwork, Purchased, NotEnough, SoldOut)
    - `EditData` `Data/TriggerActions` × 3 (Initial, Reset on Tuesday, MailingList on Wednesday)
    - `EditData` `Data/Mail` × 2 (Purchase + MailingList)
    - `Include` `Data/RealismMode.json` (when RealismMode=true)

### 3.5 Phase 3 — Re-shape the existing 11 generators

Re-target every existing generator to emit CP-shaped fragments instead of orphan files. The pack's `__init__.py` (currently 746 lines, 11 classes) shrinks because each class emits a smaller, more focused payload.

| Generator | Current output | New output |
|---|---|---|
| `ManifestGenerator` | `manifest.json` (no deps) | `manifest.json` with `Dependencies: [BroadcastAPI, Esca.EMP]`, `ConfigSchema.RealismMode`, `UpdateKeys: []` |
| `ShopItemPoolGenerator` | `assets/data/shops.tsv` | `metadata['item_pool']` (100 items: id, name, price, type, stock) — used by composer |
| `TVChannelGenerator` | `assets/data/tv_channels.json` | `metadata['channel']` (channel_id, name, icon_index, etc.) — used by composer |
| `MailSystemGenerator` | `mail/*.json` | `metadata['mails']` (purchase + mailing list) — used by composer |
| `ItemSpritesGenerator` | `assets/sprites/shop_sprites.png` + JSON | `Assets/Items/item_N.png` × 100 (curated) |
| `UIAssetsGenerator` | `assets/ui/catalog_background.png` | `Assets/TVSNLetterBackground.png` + `Assets/TVSNChannelIcon.png` + `Assets/Junk.png` |
| `CatalogPreviewGenerator` | `assets/data/catalog_preview.json` | DROPPED (replaced by composer's `Changes` array) |
| `RealismDamageGenerator` | `assets/data/damage_modifiers.json` | DROPPED (replaced by `Data/RealismMode.json`) |
| (4 more existing) | … | Audit each against the reference — most get dropped or merged |

**LLM-driven (parameters):**
- `ShopItemPoolGenerator`: LLM picks 100 item IDs from SDV's item universe, sets a price per item, picks a 1-line sales pitch per item.
- `MailSystemGenerator`: LLM drafts the 2 letters (purchase + mailing list) and the 2 channel-text templates.
- `ManifestGenerator`: LLM picks the mod name + description.

**Deterministic (wiring):**
- Everything else: `ContentPatchComposer` is a pure templating step driven by the reference's `content.json` shape.

### 3.6 Phase 4 — `Assets/Items/` and `i18n/` generators

Two new generators (or fold into existing):

- `ItemIconsGenerator` — emits 100 PNG files at `Assets/Items/item_N.png`. Source: a curated SDV icon kit (start with the reference's actual icons, then expand via the SDV sprite sheet). Each icon is 168×28 (matches the reference's `ToArea`).
- `I18nGenerator` — emits `i18n/default.json`:
  - `TVShoppingNetwork.ChannelName` (LLM-generated)
  - `TVShoppingNetwork.ChannelText.{Intro,Start,SalesTalk,SoldOut,Purchase.*}` (LLM-generated, templated)
  - `TVShoppingNetwork.ChannelText.ItemDescription.{1..100}` (LLM-generated)
  - `TVShoppingNetwork.ItemName.{1..100}` (LLM-generated)
  - `TVShoppingNetwork.Letter.{Title,Description}` (LLM-generated)
  - `TVShoppingNetwork.MailingList.{Title,Description}` (LLM-generated)
  - `TVShoppingNetwork.RealismMode.*` (LLM-generated, optional)

### 3.7 Phase 5 — `Data/RealismMode.json` (optional toggle)

When `RealismMode=true` (a `ConfigSchema` option):
- Emit `Data/RealismMode.json` with the 6 KB conditional patch.
- Composer adds `Include` Change under the `When` block.

### 3.8 Phase 6 — Smoke test against the reference

The existing `tests/test_sdv_smoke_test.py` (104 lines) tests the shell script's gating logic, not actual mod loading. Extend with:

- **`scripts/diff_against_reference.py`** — given a generated mod + the reference mod, diff:
  - `manifest.json`: same `Format`, same `Dependencies`, same `ConfigSchema` keys.
  - `content.json`: same `DynamicTokens` names, same `Changes[*].Action` set, same `Changes[*].Target` patterns.
  - `Assets/`: same filenames, same approximate dimensions.
  - `i18n/`: same top-level keys (item names 1–100, channel text, etc.).
- **`tests/test_shop_channel_parity.py`** — runs the pipeline + the diff script, asserts ≥80% parity against the reference's structure.

### 3.9 Phase 7 — Free-form `on_message` handler (the last documented follow-up)

`sdv-mod-generator/app/discord/bot.py:112 on_message` currently only handles greetings. Add:

```python
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    content = message.content.strip()
    if not content or content.startswith("!") or content.startswith("/"):
        return  # let commands handle it
    if is_greeting(content):
        await message.channel.send("Hello! ...")
        return
    if len(content) < 10:
        return  # too short, don't pipeline
    # Heuristic gate: pipe into the pipeline
    user_id = str(message.author.id)
    request_id = ...
    await run_pipeline_background(request_id, user_id, content)
    await message.channel.send(f"Generating mod... Request ID: `{request_id}`")
```

Plus a test: `tests/test_on_message.py` (greeting, bot-self, prefix-`!`, too-short, valid-prompt).

### 3.10 Phase 8 — Final cleanup

- Update `PHASES.md` — add Phase 6 row, mark post-launch follow-ups complete.
- Run `make test` + `make lint`; commit fixes.
- Final commit: `docs: mark Phase 6 complete, MVP 2.0 matches TV Shopping Network reference`.
- Clean worktree: `git worktree remove ../project-phase6 && git branch -d phase6-shop-channel-cp-shape`.

---

## 4. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM produces invalid item IDs (not in SDV) | Validate against `knowledge/data/item_ids.json` (the curated 100-item inventory), reject + retry once, fall back to default list |
| LLM doesn't fit 100 item descriptions in token budget | Two-pass: LLM generates 100 item names in batch 1, 100 sales pitches in batch 2; or raise `max_tokens` to 16384 |
| `ContentPatchComposer` ordering bugs (DynamicTokens referenced before defined) | Strict precedent: `DynamicTokens` always come before `Changes`; CP evaluates top-to-bottom |
| `EditImage` `ToArea` matches reference exactly | Pixel-test the generated `Assets/Items/` against reference (dimensions only, not pixel content) |
| 100-item PNG generation is slow | Use `assets/Items/item_N.png` from the reference directly when the user picks the same item IDs; otherwise generate a color-coded placeholder |
| RealismMode.json is 6 KB of conditional CP — error-prone to author | Treat as a static template, only the `Junk` and `Receipt` items get user-customized names via i18n |
| Free-form `on_message` triggers duplicate generations | Idempotency: reuse `request_id` if the same user+prompt has been seen in the last 5 minutes (Redis-cached) |

---

## 5. Definition of Done (MVP 2.0)

1. `git status` clean on master.
2. `make test` passes (including `test_pipeline_integration.py`).
3. `make lint` clean (mypy + ruff).
4. `make smoke-test` runs against a real SDV install + BroadcastAPI + Esca.EMP and the generated mod loads without errors.
5. `scripts/diff_against_reference.py` reports ≥80% structural parity against the reference mod.
6. A Discord user types `做一个电视购物频道` (or equivalent) and receives a DM with a mod zip that:
   - Loads in SDV with Content Patcher enabled.
   - Has the TV Shopping Network channel on Saturday.
   - Shows this week's random item with a price.
   - Lets the player buy → mail delivered → item in inventory.
   - Optionally (RealismMode=true) has a refund quest for junk items.
7. PHASES.md marks Phase 6 complete; README updated; AGENTS.md + CLAUDE.md committed.

---

## 6. Time Estimate (rough)

| Phase | Effort |
|---|---|
| 0. Pre-work (commit docs, refresh README, investigate skipped test) | 0.5 day |
| 1. Define contract + reference fixture | 0.5 day |
| 2. `ContentPatchComposer` (the 12th generator) | 1.5 days |
| 3. Re-shape 11 existing generators | 1.5 days |
| 4. `Assets/Items/` + `i18n/` generators | 1 day |
| 5. `Data/RealismMode.json` | 0.5 day |
| 6. Smoke test + diff script | 1 day |
| 7. `on_message` handler + test | 0.5 day |
| 8. Final cleanup + commit | 0.5 day |
| **Total** | **~7.5 days** |

---

## 7. File Inventory (where the work lands)

```
sdv-mod-generator/
├── generators/packs/stardew_valley/features/shop_channel/
│   ├── __init__.py                          # REWORK: 11 generators → CP-shape
│   └── content_composer.py                  # NEW: 12th generator
├── tests/
│   ├── fixtures/tvsn_reference/
│   │   └── content.json                     # NEW: CP-shape contract fixture
│   ├── test_shop_channel_contract.py        # NEW: T1 contract test
│   ├── test_shop_channel_parity.py          # NEW: diff-against-reference test
│   └── test_on_message.py                   # NEW: free-form prompt test
├── scripts/
│   └── diff_against_reference.py            # NEW: structural diff
├── app/discord/bot.py                       # EDIT: on_message handler
├── PHASES.md                                # EDIT: add Phase 6 row
├── README.md                                # EDIT: match current layout
├── AGENTS.md                                # EDIT: commit pending rewrite
└── CLAUDE.md                                # EDIT: commit pending rewrite
```

---

## 8. Open Questions for the User

1. **Item ID pool**: ship with the reference's 100 IDs (curated, hand-tuned) or let the LLM pick freely from `knowledge/data/item_ids.json` (more variety, less curated)?
2. **LLM token budget**: cap at 100 items per generation, or allow the user to request a smaller pool (e.g. 20 items)?
3. **i18n languages**: ship `default.json` only, or target a specific set (es, fr, de, ja, zh)?
4. **RealismMode**: always-on, always-off, or user-configurable?
5. **Reference-as-template (Option C) vs reference-as-spec (Option A)**: prefer AI-substituted variety, or near-clone of the reference?
