# Test Suite

## Running Tests

```bash
make        # default — runs build (test + lint)
make test   # all tests including pipeline integration
make test-quick   # unit tests only (no langgraph required)
```

## Test Structure

```
tests/
├── conftest.py              — shared fixtures
├── test_router.py           — game detection + phase routing
├── test_quality_gate.py     — T1 deterministic + T2 LLM judge
├── test_generators.py       — all 10 shop_channel + texture generators
└── test_pipeline_integration.py  — full pipeline nodes + e2e
```

## Test Counts

| Suite | Count | Notes |
|-------|-------|-------|
| Router | 9 | Game detection, phase routing, keyword mapping |
| Quality gates | 11 | T1 pass/fail, T2 skip behavior |
| Generators | 17 | All generators + GeneratorOutput unit |
| Pipeline | 11 | Skipped if langgraph not installed |
| **Total** | **49** | **38 pass, 11 skip** |

## Test Descriptions

### test_router.py
- `detect_game()` keyword matching for stardew_valley
- `route()` sets correct game + phase + generators
- Fallback to stardew_valley on unknown prompts
- All phases mapped in `_PHASE_BY_KEYWORD`

### test_quality_gate.py
- T1 passes valid manifest.json (all required fields)
- T1 fails manifest missing UniqueID
- T1 fails Shops.tsv with no data rows
- T1 fails config.json missing Enabled
- T1 passes/fails trigger_actions.json appropriately
- T2 returns skipped + score 10 when no LLM configured

### test_generators.py
- Each generator produces expected files (manifest.json, Shops.tsv, etc.)
- Each generator has fallback data when LLM unavailable
- GeneratorOutput add_file / add_asset / metadata work
- Validate passes on correct output, fails on missing fields

### test_pipeline_integration.py (requires langgraph)
- `node_route` sets game + phase + generators
- `node_generate` runs all generators in order
- `node_t1_gate` passes valid output, fails bad manifest
- `node_package` produces zip_key with request_id
- Full pipeline end-to-end for shop_channel and texture

## Adding Tests

```python
# Add to existing test file
def test_new_case():
    result = some_function(input)
    assert result == expected
```

## CI/Gate

`make build` (default goal) runs `test` then `lint`. Both must pass for a successful build.

```bash
make build  # exit 0 = all green
```
