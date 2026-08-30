# Plan — 完成 sprite 生图特性并完美接入 mod gen 管道

> software-dev-loop 产物。前置：`doc/sprite-generator-plan.md` 已把「生图 → downsample+量化 → 16×16 sprite」落地成 `sprite_utils` + `sprite_generator`，且已注册进 pack/router（提交 `52d48b2`、`0fb6777`、`319fd43`）。本 plan 收尾：把 sprite phase 从「能单跑」升级到「在完整 mod gen 管道里是一等公民」。

## Goal（不被缩小）

**完成 AI sprite 生图特性，并把它完美接入 mod gen 管道** —— sprite phase 必须：配置被文档化 + 测试隔离、输出过 T1 门（和每个生成器同等标准）、完整 Route→Generate→T1→T2→Package 管道端到端出可加载 zip（有测试证明，不只是单阶段能跑）、MiniMax 真实 API 路径跑通一次（非 mock）。

### 成功标准（审计用，不是自我声明）

1. 完整管道新增 sprite 测试：`run_pipeline("...sprite...")` → status `done`、`zip_key` 非空、`sprite_generator` 在 outputs、`t1_passed=True`。
2. T1 门新增 sprite 臂：坏 manifest（缺必填字段）被拦（红），正常 sprite 输出过门（绿）。
3. conftest 隔离 `MINIMAX_API_KEY`/`SPRITE_*`，`.env.example` 文档化这四个变量。
4. MiniMax 真实（非 mock）生图跑通一次，benchmark：量化后 `unique_colors ≤ 16` 且 `foreground_cells > 0`。
5. 全量测试套件绿（含新增测试）。

### 明确不做（防止目标漂移）

- 不回改 texture 生成器的旧字段（`SourceRect/ToRect` + `1.29.0`）——KNOWLEDGE 已记为「单独 PR，不混入 sprite 工作」。
- 不改 router、不改 packager、不改 sprite 生成器本身（已注册、已自产 manifest）。
- 不做「sprite 供其他生成器跨阶段调用」的深层集成——那需要改 pipeline 多阶段拓扑，超出「收尾」范围。

## 缺口（source-anchored，逐条核对过）

| # | 缺口 | 证据 |
|---|---|---|
| 1 | 完整管道无 sprite 测试 | `tests/test_pipeline_integration.py:299-326` `TestFullPipeline` 只有 shop_channel/texture/custom_crafting；`test_phase_manifest_isolation.py:68` 只覆盖 generate+package+SMAPI，不跑 route+T1+T2 |
| 2 | T1 门无 sprite 臂 | `quality/gate_t1.py:281-408` `_gen_specific_validation` 有 manifest_generator/shop_item_pool/.../general_author 臂，无 sprite；sprite 的 manifest 必填字段从未被门校验 |
| 3 | 配置未文档化 | `config/.env.example` 无 `SPRITE_IMAGE_PROVIDER`/`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`/`SPRITE_DETERMINISTIC`；operator 无从发现 MiniMax provider |
| 4 | 测试未隔离 sprite 变量 | `tests/conftest.py:19-53` `_isolate_test_env` 隔离了 OPENAI/ANTHROPIC/DISCORD/API/ALL_PROXY，但无 MINIMAX_API_KEY/SPRITE_*；违反 AGENTS.md「新 env var 被生产代码读 → 加进 fixture」约定 |
| 5 | MiniMax 真实 API 未跑过 | `KNOWLEDGE.md:32` 「MiniMax 真实（非 mock）API 调用尚未真机跑过一次」；请求形状靠 mocked aiohttp 钉死 |

## Approach

收尾不改架构，只补四块：

1. **T1 sprite 臂**（`gate_t1.py`）：在 `_gen_specific_validation` 加 `sprite_generator` 臂——manifest 必填字段（Format/UniqueID/Name/Version/ContentPackFor，复用 `manifest_generator` 臂的检查逻辑）+ content.json 是 dict 且 `Changes` 非空列表。sprite 自产 manifest（同 weather_event/weapon_definition/tool_definition 形态），所以它需要自己的 manifest 校验臂，不能借 `manifest_generator` 的。
2. **完整管道测试**（`test_pipeline_integration.py`）：`TestFullPipeline` 加 `test_full_pipeline_sprite`——`monkeypatch.setenv("SPRITE_DETERMINISTIC","1")` 后 `await run_pipeline("req_sprite_test","test_user","make a pixel art sprite of a glowing blue carp")`，断言 status/zip_key/outputs/t1_passed。镜像 `test_full_pipeline_shop_channel`（`test_pipeline_integration.py:300-308`）。
3. **配置文档 + 隔离**：`.env.example` 加四变量（注释说明 provider 选择）；`conftest.py` `_isolate_test_env` 加 `MINIMAX_API_KEY`/`MINIMAX_BASE_URL`/`SPRITE_IMAGE_PROVIDER`/`SPRITE_DETERMINISTIC`。
4. **MiniMax 真实验证**（一次性）：写临时脚本加载根 `.env` 的 `MINIMAX_API_KEY`，调 `_generate_minimax_image("a glowing blue carp fish")` → `downsample`+`quantize` → 打印 `unique_colors`/`foreground_cells`（用 `scripts/benchmark_sprite.py` 的同款指标）。只跑一次，跑完即删临时脚本。**不打印 API key。**

## Seams / 测试边界

- 只新增测试 + 门检查，不改被测实现（sprite 生成器、packager、router 都不动）。
- 完整管道测试用 `SPRITE_DETERMINISTIC=1`（无网络、无 key、确定性），镜像现有 phase-isolation 的做法（`test_phase_manifest_isolation.py:74-80`）。
- T1 臂的红绿用现有测试风格：坏 manifest 直接构造 `GeneratorOutput` 喂 `run_t1`（镜像 `test_pipeline_integration.py:96-140` 的 T1 测试手法）。

## 风险

- **load_dotenv 顺序（critic 已纠正本 plan 的原分析）**：原 plan 担心 `SPRITE_DETERMINISTIC=1` 被 `load_dotenv(override=True)` 清掉——**不会发生**：`config/.env` 里没有 `SPRITE_*`/`MINIMAX_*` 变量，`load_dotenv` 只写 `.env` 文件里存在的 key，不动 monkeypatch 设的值。真正的风险是镜像面：`run_pipeline` 懒 import `app.config`（pipeline.py:404）发生在 conftest 删掉 `OPENAI_API_KEY` 之后，`load_dotenv(override=True)` 会**重新注入** `OPENAI_API_KEY`，让 T2 门走 `get_client()` 发真实 LLM 调用而非「No LLM provider」回退。这是**预先存在**的（3 个既有 full-pipeline 测试同享），顺序依赖：哪个 full-pipeline 测试先首次 import `app.config` 谁触发重注入。本 host 上全套 24s 跑完（1268 passed），说明 `app.config` 在 collection 期已被别的测试模块 import、monkeypatch 生效、T2 跳过——但换一台 `config/.env` 带 key 的机器会变慢/挂起。已记入 KNOWLEDGE「Gotcha」，不在本 plan 修（改共享测试基建，超出收尾 scope）。
- **默认 `openai` sprite provider 潜藏失效**：`SPRITE_IMAGE_PROVIDER` 默认 `openai` → `_generate_openai_image` 用 `OPENAI_BASE_URL` + `/images/generations` + `gpt-image-1.5`。但 `OPENAI_BASE_URL` 默认（config.py:71）及 `.env` 都是 `api.minimaxi.com/v1`（MiniMax 的 chat 端点，只服务 M2.7，不服务 gpt-image-1.5）。所以默认真实路径会失败；只有显式 `SPRITE_IMAGE_PROVIDER=minimax` 走通的 `_generate_minimax_image` 可用。已记入 KNOWLEDGE，并在 `.env.example` 加约束说明（不改生成器本体——超出 scope）。
- **MiniMax 真实调用成本/网络**：单次 image-01 生成成本极低，但依赖网络可达 + key 有效。若失败，如实报告 blocker，不伪造结果。真实调用已跑通：23171 色 → 16 色、前景格 53、PASS。

## critic 复核结论（2026-08-30，fresh-context 子代理）

五个缺口全部 CONFIRMED（source-anchored 无误）。逐改动 verdict：T1 臂 CORRECT（略超 plan spec 但贴合 manifest_generator/content_json_generator 既有臂）；full-pipeline 测试 CORRECT（plan 的 CHANGES 清单漏了 T1 红绿两测——实现已补）；conftest CORRECT；`.env.example` CORRECT（`SPRITE_DETERMINISTIC` 保持注释而非可设值，防 footgun）；MiniMax 脚本的 benchmark 判据 `unique_colors≤16` 在 quantize 后是恒真的（本验证补了「源图 23171 色」作真实信号，非空图证明）。阻塞项 2 条（load_dotenv 顺序需重验、plan 落后于已实现 diff），非阻塞 4 条，均已吸收进本 plan。
