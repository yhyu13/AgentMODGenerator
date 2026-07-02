# SDV Mod Generator — Build Status

**Last updated:** post-Phase 5 deploy. Reference mod: [TV Shopping Network](https://ggmods.com/game/stardew-valley/mod/147/) (Airyn) — Content Patcher mod with TV shopping channel, weekly random items, mail delivery, broken-item refund. Deps: Content Patcher + BroadcastAPI + Esca's Modding Plugins.

---

## Phase Status

| Phase | Scope | Status | Merged in |
|---|---|---|---|
| 0 | Project skeleton, storage layer | done | early |
| 1 | API endpoints + status queries | done | early |
| 2 | Discord bot (gateway) | done | `phase-3-generators-feedback` |
| 3 | Generator coverage (TV/mail/NPC/event/etc.) | done | `phase-3-generators-feedback` |
| 4 | Quality system (T1 schema, T2 3-judge panel, feedback router) | done | `phase4.5-mvp-quality` |
| 4.5 | MVP quality hardening | done | `phase4.5-mvp-quality` |
| 4.6 | Pre-P5 hardening (mail/SMAPI/bot ready/T2 contract) | done | `phase4.6-pre-p5-hardening` |
| 5 | Deploy surface (secrets, containerize, health/metrics, smoke test, runbook) | done | `phase5-deploy` |
| Post-5 | NPC schedule phase, LLM retry backoff, progress %, Discord UX, test fixes | done | `master` |

**Current state:** all planned phases shipped. The next work is operational hardening and the post-launch follow-ups below — not new features.

---

## Discord Integration

Two complementary paths, both live in `sdv-mod-generator/app/discord/`:

- **Gateway bot** ([`bot.py`](sdv-mod-generator/app/discord/bot.py)) — discord.py WebSocket connection, runs pipeline in-process. Slash commands: `/generate`, `/status`, `/cancel`, `/history`, plus `/download` and `/phases` added later. Requires `ALL_PROXY` and the privileged `MESSAGE_CONTENT` intent.
- **HTTP webhook** ([`webhook.py`](sdv-mod-generator/app/discord/webhook.py)) — pure HTTP interactions endpoint mounted at `POST /webhooks/discord`. Used when the gateway isn't viable (serverless, blocked WebSocket egress). Includes Ed25519 signature verification and `send_completion_webhook` for push notifications.

The dead `commands.py` draft was removed.

---

## Open Follow-ups (post-launch, not yet scheduled)

- **Free-form `on_message` prompt handling** — today the bot only greets in chat. Add an LLM/heuristic gate that pipes non-trivial messages into `run_pipeline_background`. ~40 lines in `bot.py:on_message`.
- **Completion-push DM** — the slash command returns a `request_id` and the user has to poll `/status`. Add a Redis-backed notifier watcher that DMs the zip on `done`/failure. New `app/discord/notifier.py` + 4 small edits.
- **Ed25519 webhook signature** — `webhook.py:verify_signature` is currently a stub that always returns True. Implement proper Ed25519 via PyNaCl.
- **Test stability** — agent-generated tests have been corrected in `01335d0`; the suite should be re-run on CI to confirm green.
- **Stale doc** — `docs/DUAL_AGENT_RUN_2026-06-12.md` is a working doc and should probably be moved to `docs/runs/` or deleted before tagging a release.

---

## Reference Mod

[TV Shopping Network](https://ggmods.com/game/stardew-valley/mod/147/) (Airyn) — Content Patcher mod with TV shopping channel, weekly random items, mail delivery, broken-item refund. Deps: Content Patcher + BroadcastAPI + Esca's Modding Plugins. This is the bar MVP 2.0 aims to match.
