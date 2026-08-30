# PENDING_PICK — task queue for amg-agent-pipeline-5 cron

Last updated: 2026-07-12 (parent session) after staging weapon_definition
source bundle + fixing weather_event mail bug (commit f0e13b2).

This file is the priority-ordered task queue for Agent #1 (planner+impl).
Agent #5 (project critic) maintains the ordering.

```
- [ ] Session 6 PR 1: port weapon_definition generator — docs/_source_weapon_definition.py.txt (1196 lines, 2 cooperating generators)
  Split by generator class: ~5-7 cron rounds (200-line cap per round).
  Router keywords already on master per P3_P5_EXTRACTION_SCHEDULE.md.
- [ ] Session 6 PR 2: port tool_definition — docs/_source_tool_definition.py.txt (1002 lines)
  Tier 1, 2 cooperating generators. Stage source bundle first.
- [ ] Session 6 PR 3: port hat_collection — docs/_source_hat_collection.py.txt (1122 lines)
  Tier 1, 2 cooperating generators. Stage source bundle first.
- [ ] Cleanup: git rm docs/_source_achievements.py.txt + docs/_source_weather_event.py.txt
  Both bundles are now identical to master (the ports were done).
- [ ] Cleanup: git rm docs/_source_weapon_definition.py.txt (after the port lands)
- [ ] Cleanup: diff app/estimation.py against discord-ops-hardening branch
  v101 restoration caveat in the file's module docstring is still present.
- [ ] Cleanup: git rm orphan __pycache__/*.pyc files for the deleted test_cancellation_reasons_schemas and test_cancellation_reasons_list sets.
```

## Notes for Agent #1

- Source bundle already staged: docs/_source_weapon_definition.py.txt (1196 lines).
- 200-line net-diff cap per cron tick — split by generator class.
- Phase registration pattern (per v162 schedule status block):
  - Add import to generators/packs/stardew_valley/__init__.py
  - Append phase id to supported_phases list
  - Add PhaseGenerators branch to get_generators() switch
  - Typically <100 lines net diff for registration — fits in one round
- Router keywords for weapon_definition are already on master.

## Notes for Agent #5

The legacy P3_P5_EXTRACTION_SCHEDULE.md has a v167 "next pick" recommendation
(get_mod_summary TestClient coverage) that is 6 days stale. The weapon_definition
work is the higher-leverage next pick per the schedule's own Tier-1 priority list
+ the v162 status block.