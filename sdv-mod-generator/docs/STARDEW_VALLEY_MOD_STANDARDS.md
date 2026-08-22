# Stardew Valley Mod Quality Standards

> **Audience**: LLM generators producing Content Patcher mod content for the
> SDV Mod Generator pipeline (generators in `generators/packs/stardew_valley/features/*/`).
> This document is the single source of truth for what "good" looks like.
> It is fed to the LLM via `llm_system_prompt()` in `generators/llm_utils.py`.
>
> **Source of truth**: This doc is derived from (a) the T2 3-judge panel's
> actual feedback on generated mods (2026-07-09 audit of `req_08628445042f`
> and the cron-archived 162 rounds of generated test mods), and (b) the
> real Content Patcher mod format spec at <https://github.com/Pathoschild/StardewMods>.

---

## 1. The 5-7 file standard: every mod MUST contain these files

A Stardew Valley Content Patcher mod **must** contain at least these 5 files
in its root directory inside the zip:

| File | Required? | Purpose | Format |
|------|-----------|---------|--------|
| `manifest.json` | **YES** | Mod identity (UniqueID, Name, Version, Author, Description, Dependencies) | JSON |
| `content.json` | **YES** | The patch instructions (Load/EditData/EditMap/EditImage actions) | JSON |
| `README.txt` | YES | Installation + compatibility notes (the packager adds this) | Plain text |
| Optional `i18n/default.json` | NO | Default-language strings (auto-generated if manifest has `i18n` block) | JSON |
| Game-data files (mail/*.txt, assets/*, etc.) | NO | Mod-specific content; only present if the mod adds them | varies |

**Critical**: `manifest.json` is a **hard requirement**. A mod without it
**will not load** in SMAPI. The T2 judge panel **flags missing manifest.json
as a critical failure** (TechnicalComplianceJudge score = 1).

### 1.1 manifest.json shape (canonical)

```json
{
  "Format": "1.29.0",
  "UniqueID": "YourName.YourModName",
  "Name": "Your Mod Display Name",
  "Description": "One-paragraph description of what the mod does.",
  "Version": "1.0.0",
  "Author": "YourName",
  "ContentPackFor": {
    "UniqueID": "Pathoschild.ContentPatcher",
    "MinimumVersion": "2.4.0"
  },
  "Dependencies": [
    { "UniqueID": "Pathoschild.ContentPatcher", "MinimumVersion": "2.4.0" }
  ]
}
```

`Format`, `UniqueID`, `Name`, `Version` are **required**; everything else is
recommended. `UniqueID` must be a single dotted string (no spaces); snake_case
is the convention (e.g. `yourname.weather_lullaby`).

### 1.2 content.json shape (canonical)

```json
{
  "Format": "1.29.0",
  "Changes": [
    {
      "Action": "EditData",
      "Target": "Data/SomeDataFile",
      "Entries": { "key": "value" },
      "When": { "Weather": "rainy" }
    }
  ]
}
```

`Format` (Content Patcher version) is required. `Changes` is required. Each
Change's `Action` must be one of: `Load`, `EditData`, `EditMap`, `EditImage`,
`EditAsset`, `Include`, `TextOperations`. **Custom action names are invalid**;
the LLM must use the exact 7 strings above.

## 2. Game-data file conventions (the parts the LLM gets wrong most)

### 2.1 mail files: plain text, mail ID is filename, body is the file's only content

**WRONG** (what the cron currently does):
```
mail/weather_announcement.json  ← JSON object, mail body inside
mail/storm_warning.json          ← JSON object, mail body inside
```

**CORRECT** (what Content Patcher expects for mail):
```
mail/rainy_day_forecast.txt     ← plain text body, NO JSON wrapper
mail/storm_warning.txt           ← plain text body, NO JSON wrapper
```

The mail ID is the filename minus `.txt`. The file's content is the body.
The player reads this in-game as a letter from an NPC. **No JSON, no metadata,
no `From:` line** — just the body text. SDV wraps the body in its own UI.

Body length: 1-3 sentences. Use `@` for the player name. Use `^` for line
breaks if you need them. Don't use Markdown — SDV displays plain text.

### 2.2 Buffs: use `Data/Buffs` EditData, NOT custom JSON

**WRONG** (what the cron currently does):
```
assets/data/weather_buffs.json   ← custom JSON file SDV doesn't read
```

**CORRECT** (SDV 1.6 `Data/Buffs` shape):
```json
{
  "Action": "EditData",
  "Target": "Data/Buffs",
  "Entries": {
    "rainy_day_fishing": {
      "DisplayName": "Rainy Day Fishing",
      "Description": "+3 Fishing while it's raining.",
      "IconTexture": "TileSheets\\BuffsIcons",
      "IconSpriteIndex": 1,
      "Duration": 300000,
      "Effects": { "FishingLevel": 3 }
    }
  }
}
```

The buff ID is the key. `IconTexture` is the icon sheet (vanilla:
`TileSheets\BuffsIcons`); `IconSpriteIndex` is the icon slot (0-23).
Duration is **milliseconds** (300000 = 5 minutes, 600000 = 10 min).
Effects keys are skill/stat fields: `FarmingLevel`, `FishingLevel`,
`MiningLevel`, `ForagingLevel`, `LuckLevel`, `CombatLevel`, `MaxStamina`,
`Speed`, `Defense`, `Attack`, `Immunity`, `MagneticRadius`.
A `Data/Buffs` entry does nothing by itself — apply it with
`Data/TriggerActions` `AddBuff` (weather) or `Data/Objects` `Buffs` (food).

**Do NOT** create custom `weather_buffs.json` files. SDV will not read them.

### 2.3 NPC dialogue: use `Characters/Dialogue/<NPC>` EditData, NOT custom JSON

**WRONG** (what the cron currently does):
```
assets/data/weather_dialogue.json   ← SDV doesn't read this
```

**CORRECT** (Content Patcher per-NPC dialogue):
```json
{
  "Action": "EditData",
  "Target": "Characters/Dialogue/Abigail",
  "Entries": {
    "Weather_Rainy": "The rain makes me want to stay inside and play video games. Want to join me, @?"
  },
  "When": { "Weather": "rainy" }
}
```

Each NPC's dialogue is a separate EditData block. The key naming convention
is `Weather_<Condition>` (e.g. `Weather_Rainy`, `Weather_Snowy`). The `When`
block filters when the dialogue is shown — without it, the dialogue is always
active. The 36 vanilla NPC names are: Abigail, Alex, Caroline, Clint, Demetrius,
Elliott, Emily, Evelyn, George, Gus, Haley, Harvey, Jas, Jodi, Kent, Krobus,
Leah, Lewis, Linus, Marnie, Maru, Morris (Joja), Pam, Penny, Pierre, Robin,
Sam, Sandy, Sebastian, Shane, Vincent, Willy, Wizard. Use exact capitalization.

NPC dialogue style guidelines: keep under 120 characters, use `@` for player
name, stay in-character (Abigail is emo + gamer, Sebastian is introverted,
Penny is nurturing, Willy is the friendly fisherman, etc.).

### 2.4 Weather buffs: use `Data/TriggerActions` + `Data/Buffs`

There is **no** `Data/WeatherEvents` asset in SDV 1.6. Apply a weather
buff with `Data/TriggerActions` (`DayStarted` + `WEATHER` game-state query
+ `AddBuff`) and define the buff in `Data/Buffs`.

```json
{
  "Action": "EditData",
  "Target": "Data/TriggerActions",
  "Entries": {
    "spring_rain_blessing": {
      "Id": "spring_rain_blessing",
      "Trigger": "DayStarted",
      "Condition": "WEATHER Here Rain, SEASON Spring",
      "Actions": ["AddBuff rainy_day_farming"],
      "MarkActionApplied": false
    }
  }
}
```

`WEATHER Here` values are **PascalCase** (`Rain`, `Storm`, `Snow`, `Wind`,
`Sun`). The `When: { "Weather": ... }` filter is **lowercase** (`rainy`,
`stormy`, `snowy`, `windy`, `sunny`). Set `MarkActionApplied` false so the
buff can apply again the next matching day.

## 3. Manifest.json — what to include

A mod's `manifest.json` should reflect the **scope** of what the mod does.
The LLM should generate:

- **UniqueID**: `ai_generator.<mod_slug>` (the pack's author prefix +
  lowercase, snake-cased mod name). Never use spaces. Examples:
  - "add a TV shopping channel" → `ai_generator.tv_shopping_channel`
  - "weather event for rainy days" → `ai_generator.rainy_weather_events`
- **Name**: 3-7 words, descriptive. "Rainy Day Weather Events" not "Weather".
- **Description**: 1-2 sentences. Specific to the mod's content, not generic.
  **Good**: "Adds 5 weather events with rain-themed buffs, NPC dialogue
  changes, and weather forecast mail for the rainy season."
  **Bad**: "A weather mod that adds stuff."
- **Version**: always "1.0.0" for new mods.
- **Author**: "AI Generator" (the canonical author for LLM-generated mods).
- **Dependencies**: always include `Pathoschild.ContentPatcher` ≥ 2.4.0.

## 4. Content quality bar (T2 judge criteria)

The T2 panel has 3 judges. The mod passes T2 if **at least 2 of 3** judges
score ≥ 7/10. Default scoring rubric:

### 4.1 GameBalanceJudge (passes if score ≥ 7)

- **Buff values**: 1-5 per stat is the sweet spot. Anything > 5 is "broken
  economy". Anything < 1 is "invisible". Aim for 2-3.
- **Buff duration**: 300-1800 seconds (5-30 min). Anything > 3600 is "broken
  pacing". Anything < 120 is "imperceptible".
- **Trigger frequency**: a buff that fires on every rainy day is fine; a buff
  that fires every 5 minutes is too frequent.
- **Item values**: prices should be 2-5x the vanilla equivalent. A 100g
  parsnip is suspicious; a 500g parsnip is broken.

### 4.2 ContentQualityJudge (passes if score ≥ 7)

- **Naming**: use evocative, game-themed names. "Spring Rain Blessing" is
  good; "rain_buff_1" is bad. "Stormy Mining" is good; "storm" is bad.
- **Descriptions**: 1 sentence, evocative, in-character. "The gentle spring
  rain nurtures your crops, granting bonus farming experience" is good;
  "rain helps" is bad.
- **Dialogue**: in-character, brief, charming. "I love the rain, @. It makes
  everything feel so peaceful" is good. "rain rain go away" is bad.
- **Variety**: the mod should cover multiple weather conditions or seasons,
  not just "rainy" (unless the prompt explicitly asked for just one).
- **Thematic consistency**: all content should share a theme. Don't mix
  weather buffs with TV channel content.

### 4.3 TechnicalComplianceJudge (passes if score ≥ 7)

- **Required files present**: `manifest.json`, `content.json` MUST be in
  the zip. Missing either = critical failure.
- **File format**: mail is plain text, not JSON. Custom game-data files
  (e.g. `weather_buffs.json`) are forbidden — use `Data/Buffs` EditData
  instead. The only JSON files in the zip root should be `manifest.json`,
  `content.json`, and any `i18n/*.json`.
- **Action names**: use the exact 7 Content Patcher action names
  (`Load`, `EditData`, `EditMap`, `EditImage`, `EditAsset`, `Include`,
  `TextOperations`). Custom actions like `AddBuff` or `CreateMail` are
  invalid — they have to be done via EditData on the right target.
- **Path casing**: `Data/...` (PascalCase on directory), but file paths in
  custom file edits use lowercase (e.g. `mail/rainy_day.txt` not
  `Mail/Rainy_Day.txt`).
- **Valid Pydantic schema**: each LLM response must validate against its
  generator's Pydantic output model. If validation fails, the generator
  falls back to a hardcoded payload.

## 5. Naming conventions (the LLM's gotcha list)

The LLM must use these conventions consistently:

| Context | Convention | Example |
|---------|-----------|---------|
| `manifest.json` `UniqueID` | snake_case, dot-separated, lowercase | `ai_generator.rainy_weather_events` |
| `manifest.json` `Name` | Title Case, 3-7 words | "Rainy Weather Events" |
| Trigger IDs (`Data/TriggerActions` keys) | snake_case | `spring_rain_blessing` |
| Buff IDs (`Data/Buffs` keys) | snake_case | `rainy_day_fishing` |
| Dialogue keys | PascalCase, condition-first | `Weather_Rainy`, `Weather_Storm` |
| Mail filenames | snake_case, `.txt` not `.json` | `rainy_day_forecast.txt` |
| NPC names | Exact vanilla capitalization | `Abigail`, `Sebastian`, `Penny` |
| Stats (in Buffs `Effects`) | TitleCase skill fields | `FishingLevel`, `FarmingLevel` |
| Weather values in `WEATHER` GSQ | PascalCase | `Rain`, `Storm`, `Snow` |
| Weather values in `When` blocks | lowercase | `rainy`, `stormy`, `snowy` |

## 6. The 12-mod anti-pattern list (rejections the T2 panel has issued)

Don't do these:

1. **Missing manifest.json** — critical failure, mod won't load.
2. **mail/*.json** — wrong format, should be `mail/*.txt` plain text.
3. **Custom `weather_buffs.json` or `weather_dialogue.json`** — SDV doesn't
   read these. Use `Data/Buffs` and `Characters/Dialogue/<NPC>` EditData.
4. **Single-word names** like "Storm" or "Buff" — too generic.
5. **Mechanical descriptions** like "+2 Farming for 300 seconds" — should be
   evocative ("The gentle rain nurtures your crops, granting bonus farming
   experience").
6. **Out-of-character dialogue** like "Sebastian: Let's dance in the rain" —
   Sebastian is an introvert; he would say "I can hear the rain from my
   room. It's actually kind of peaceful. No one bothering me."
7. **Buffs over 5 in value** — breaks the game's economy.
8. **Triggering every 5 minutes** — too frequent, breaks pacing.
9. **Covering only one weather/season when more would be natural** — the LLM
   should expand the prompt's scope unless explicitly told not to.
10. **No variety in buff stats** — if all 5 buffs are +Fishing, that's lazy.
    Mix Farming, Fishing, Mining, Foraging, Luck, Energy, Health.
11. **Empty `Changes: []` in content.json** — the mod generates nothing.
12. **`Format` field wrong or missing in content.json** — required field,
    must be the Content Patcher version (currently "1.29.0").

## 7. The 5-step recipe for a high-scoring mod

When a generator LLM is producing content for any Stardew Valley mod:

1. **Generate manifest.json FIRST** — before any other content, so the
   downstream pack has a name + UniqueID to reference.
2. **Generate the structured content** (events, dialogue, buffs, mail) with
   the right naming conventions (table above).
3. **Wrap each piece in EditData** targeting the right `Data/...` file
    (Data/TriggerActions, Data/Buffs, Characters/Dialogue/<NPC>,
    Data/mail). Do NOT use custom JSON files. Do NOT invent assets.
4. **Assemble content.json** with all the EditData blocks. Include the
   `Format: "1.29.0"` field.
5. **Mail files: plain text, .txt extension, mail ID as filename**. No JSON
   wrapper.

## 8. The packager-side filter (intermediate files are stripped)

The packager at `generators/packager.py` includes ALL files from each
generator's `out.files` dict in the final zip. Intermediate files like
`weather_buffs.json` and `weather_dialogue.json` (which the LLM-driven
generators create so the deterministic `WeatherContentJsonGenerator` can
read them) end up in the final zip — that's the bug the T2 panel flagged.

**The fix** (in this session): have the LLM-driven generators NOT create
those intermediate files. Instead, the `WeatherContentJsonGenerator`
should read the LLM output via `inp["prior_outputs"]` directly (which it
already does) and the intermediate `weather_buffs.json` and
`weather_dialogue.json` files should be stripped by the packager.

**Current state** (2026-07-09): the LLM-driven generators DO create those
intermediate files. The packager does NOT strip them. This is a known
bug being fixed in this session's PR.

---

## 9. Worked example: full high-scoring weather mod

Given the prompt "add a weather event for rainy days", here's what a
high-scoring (T2 score 9/10) mod would look like:

**manifest.json**:
```json
{
  "Format": "1.29.0",
  "UniqueID": "ai_generator.rainy_day_blessings",
  "Name": "Rainy Day Blessings",
  "Description": "Adds 5 season-themed weather events with matching buffs, NPC dialogue, and weather forecast mail for the rainy season.",
  "Version": "1.0.0",
  "Author": "AI Generator",
  "ContentPackFor": {
    "UniqueID": "Pathoschild.ContentPatcher",
    "MinimumVersion": "2.4.0"
  }
}
```

**content.json**:
```json
{
  "Format": "1.29.0",
  "Changes": [
    {
      "Action": "EditData",
      "Target": "Data/Buffs",
      "Entries": {
        "rainy_day_farming": {
          "DisplayName": "Rainy Day Farming",
          "Description": "+2 Farming while it's raining.",
          "IconTexture": "TileSheets\\BuffsIcons",
          "IconSpriteIndex": 0,
          "Duration": 300000,
          "Effects": { "FarmingLevel": 2 }
        }
      }
    },
    {
      "Action": "EditData",
      "Target": "Data/TriggerActions",
      "Entries": {
        "spring_rain_blessing": {
          "Id": "spring_rain_blessing",
          "Trigger": "DayStarted",
          "Condition": "WEATHER Here Rain, SEASON Spring",
          "Actions": ["AddBuff rainy_day_farming"],
          "MarkActionApplied": false
        }
      }
    },
    {
      "Action": "EditData",
      "Target": "Characters/Dialogue/Abigail",
      "Entries": {
        "Weather_Rainy": "The rain makes me want to stay inside and play video games. Want to join me, @?"
      },
      "When": { "Weather": "rainy" }
    }
  ]
}
```

**mail/rainy_day_forecast.txt** (plain text body):
```
Dear @,

A gentle rain is expected over the next few days. Don't forget your watering
can — the rain will take care of your crops! Consider spending time fishing
or visiting the mines while waiting for the sun to return.

- The Forecast Channel
```

(No JSON wrapper, no metadata, just the body text.)

---

## 10. Quick checklist before submission

Before the LLM declares a mod "done", verify:

- [ ] `manifest.json` exists with `Format`, `UniqueID`, `Name`, `Version`
- [ ] `content.json` has `Format: "1.29.0"` and a non-empty `Changes` array
- [ ] All EditData targets are `Data/...` paths (not custom paths)
- [ ] No `mail/*.json` files — all mail is `mail/*.txt` plain text
- [ ] No `weather_buffs.json`, `weather_dialogue.json`, or other custom
      game-data JSON files
- [ ] NPC names are exact vanilla capitalization
- [ ] Event/buff/mail IDs are snake_case
- [ ] Dialogue keys are `Weather_<Condition>` PascalCase
- [ ] All buff values are 1-5
- [ ] All buff durations are milliseconds (120000-3600000)
- [ ] Mod description is specific (not "adds stuff")
- [ ] Mod covers a reasonable variety (not just 1 weather/season if more
      would be natural)

If all boxes are checked, the mod will likely score 8-10 on the T2 panel.
If any are missed, expect 1-3 judge failures and a "ship anyway" advisory
from the pipeline (default `MAX_T2_ITERATIONS=0`).

---

## Appendix: where this doc gets fed to the LLM

```python
# generators/llm_utils.py
def llm_system_prompt() -> str:
    standards_path = Path(__file__).parent.parent / "docs" / "STARDEW_VALLEY_MOD_STANDARDS.md"
    standards = standards_path.read_text() if standards_path.exists() else ""
    return f"""You are a Stardew Valley Content Patcher mod generator.

You generate valid JSON for Content Patcher mod files following the
quality standards in the bundled STARDEW_VALLEY_MOD_STANDARDS.md document.

The full standards doc is included below — read it before producing output:

{standards}

Output ONLY valid JSON matching the expected schema — no markdown, no
explanation. All paths use forward slashes (/) not backslashes. Prices are
in gold (g)."""
```

This is the system message sent on every LLM call. The LLM is expected to
read and apply the standards before generating any content.
