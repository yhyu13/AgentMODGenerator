# Pending Commit v1
- files: orchestrator/feature_flags.py
- source: cleanroom; branch file not available
- target: master working tree
- task: minimal feature-flag helper — is_enabled / record_override / list_pins / get_history with process-local override store
- verify: pytest tests/ -k feature_flag (or "no tests yet — first PR is the helper itself")
- notes: cleanroom port because the discord-ops-hardening source file (567 lines, dfb3dd7) was not on disk in this session — only the __pycache__ .pyc remained. The merge plan (docs/P3_P5_MERGE_PLAN.md) confirmed the helpers' names and the one consumer call site (`gate_t2.py` uses `is_enabled("t2_three_judge_panel")`). This first PR is just the in-memory helper: persistence, rollout percentages, and admin endpoints remain out of scope until the rest of the rollout stack lands. Style follows orchestrator/feedback_router.py (structlog snake_case, English docstrings, type annotations, Python 3.11+). Zero external deps beyond stdlib + structlog. The four defaults mirror gates/middleware that already run on master.