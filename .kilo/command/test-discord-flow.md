# SDV Mod Generator - Discord Flow Test

## Purpose
End-to-end test mimicking Discord bot `/generate` command flow.

## Usage
```bash
cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator
PYTHONPATH=. python /tmp/test_discord_flow.py
```

## What it tests
1. POST /v1/mods/generate — non-blocking, returns request_id immediately
2. Poll GET /v1/mods/{id} — until status is "done" or "failed"
3. Verify ZIP — exists, contains manifest.json, extract UniqueID/Name

## Test prompts
- `做一个电视购物频道` (Chinese)
- `Create a TV shopping channel mod` (English)

## Expected results
- Submit: returns within 1s with request_id
- Pipeline: completes in ~60-120s
- ZIP: 15 files, valid manifest.json, Content Patcher format

## On failure
- Check LLM API keys: ANTHROPIC_API_KEY or OPENAI_API_KEY
- Check Redis: redis-cli ping
- Check server logs: background_process logs
- Check ZIP output: ls /tmp/sdv-mod-generator/outputs/mods/
