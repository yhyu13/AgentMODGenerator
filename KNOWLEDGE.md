# KNOWLEDGE.md — AgentMODGenerator 长期记忆

> software-dev-loop 产物。新会话先读这里，避免重新发现。按「决策 / 事实 / gotcha / 下一步」四栏，数字带参照物，缺材料标待确认。

## 决策（为什么这么做）

- **多模态贴图走「生图出内容 + 程序化后处理」路线，不做"直接生成"** —— 实测两个生图模型都不能自发产出严格 16×16 像素画，但 downsample + 量化后形状保留。后处理是桥，不是可选。
- **`sprite_utils` 拆成 `downsample` / `quantize` 两个纯函数** —— 尺寸收缩和色板压缩是两个职责，拆开各自可测、可独立换算法。量化时「最亮簇合并成一个透明色」是 CP 硬要求（近白抖动 249/251/252 必须变成一个洞）。
- **新 sprite 生成器用现代 CP 字段，不回头改旧 texture 生成器** —— 精准修改原则。texture 的 `SourceRect/ToRect` + `Format 1.29.0` 是旧语法，留着待单独修。
- **`sprite` 关键词从 texture 改路由到新 sprite phase** —— 「生成新像素画」和「替换旧贴图」是两个能力，不能再让 `sprite` 词撞到 texture 替换：`sprite`→sprite（生图）、`texture`/`image`→texture（替换）。连带把 `test_texture_routing` 的 prompt 从 "replace the parsnip crop sprite" 改成 "replace the parsnip crop texture" 消除歧义。sprite 生成器自产 manifest.json，故不挂共享 `manifest_generator`（同 weather_event/weapon_definition/tool_definition 形态）。
- **MiniMax provider 走独立分支 + `decode_image` 嗅探格式** —— gpt-image 返回 PNG、MiniMax 返回 JPEG，两 provider 在 `_generate_sprite_image` 里按 `SPRITE_IMAGE_PROVIDER`（默认 `openai`）分发；`decode_image` 嗅探 magic（PNG 签名 / `FF D8 FF`）→ `decode_png`（纯 stdlib）/ `decode_jpeg`（Pillow）。Pillow 只懒加载给 JPEG，确定性 PNG 路径仍零依赖。JPEG 解码只能靠 Pillow（stdlib 无 JPEG 解码），不能为保「纯 stdlib」拒接 MiniMax。

## 事实（数字 + baseline）

- **生图模型不产严格像素画**：gpt-image-1.5 生成 1024×1024 → 29201 色、1.1% 的 16×16 块纯色；MiniMax image-01 → 23978 色、24.7% 纯色块、背景 30% 纯白。要 ≤8 色，实际差三个数量级。
- **benchmark（后处理有效性）**：真实生图 23182 色 → 量化后 16 色、前景格 34、`verdict PASS`。脚本 `scripts/benchmark_sprite.py`。
- **真机 load**：sprite demo zip 被 SMAPI 加载为 content pack，CP 应用 EditImage 补丁、零警告（日志 `Patched game code → Sprite Mod`）。全量 1260 passed, 12 skipped（MiniMax provider 接入后）。
- **sprite phase 已接入管道**：`route("make a pixel art sprite...")` → phase `"sprite"`、generators `["sprite_generator"]`；phase-isolation 测试以 `SPRITE_DETERMINISTIC=1` 覆盖 sprite 单阶段产可加载 zip（静态 SMAPI 校验过）。
- **MiniMax provider 已接入**：`SPRITE_IMAGE_PROVIDER=minimax` + `MINIMAX_API_KEY` → `POST api.minimaxi.com/v1/image_generation`（`image-01`、512×512、`response_format=base64`、`n=1`、`prompt_optimizer=False`），解析 `base_resp.status_code` + `data.image_base64[0]`，JPEG 经 `decode_image` 解码。mocked aiohttp 测试覆盖请求形状 + 错误 status 2049 + provider 分发。
- **EditImage 字段权威锚点**：生产包 `.reference_mods/TV Shopping Network/content.json:820-854` 用 `FromArea`/`ToArea` + `PatchMode` + `Format 2.0.0`。
- **sprite phase 已完成完整管道集成（2026-08-30）**：`test_full_pipeline_sprite` 用 `SPRITE_DETERMINISTIC=1` 跑完整 Route→Generate→T1→T2→Package 图，断言 status done + zip_key + sprite_generator in outputs + t1_passed。T1 门新增 `sprite_generator` 臂（manifest 必填字段 Format/UniqueID/Name/Version/ContentPackFor + content.json Changes 非空），坏 manifest 被拦（红）、正常 sprite 过门（绿）。全量 1268 passed, 12 skipped（skip 全是 Windows 无 bash 的脚本测试）。
- **MiniMax 真实 API 已跑通（非 mock）**：`_generate_minimax_image("a glowing blue carp fish")` 直连 `api.minimaxi.com/v1/image_generation` → 512×512 JPEG、23171 色 → downsample+quantize → 16 色、前景格 53、verdict PASS。此前只靠 mocked aiohttp 钉死请求形状，现已用真 key 验证。
- **sprite 配置已文档化 + 测试隔离**：`.env.example` + `prod.env.example` 记录 `SPRITE_IMAGE_PROVIDER`/`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`/`SPRITE_DETERMINISTIC`；`conftest._isolate_test_env` 隔离这四变量（AGENTS.md「新 env var → 加进 fixture」约定）。生成器读 os.environ 直接（同 `GENERAL_AUTHOR_DETERMINISTIC` 惯例），不走 `Config` dataclass。
- **真实 sprite demo（2026-08-30）**：auto-detect 生效（未设 `SPRITE_IMAGE_PROVIDER`，靠根 `.env` 的 `MINIMAX_API_KEY` 路由到 minimax），3 个 prompt 各生成一张 16×16 sprite + 打包成合法 CP zip（SMAPI 静态校验 0 错误）。色板证实对题：fish=全蓝青（#74c4e0 系，47 前景格）、ruby=全红（#a81e39 系，60 前景格）、sword=全金（#f6e6b6 系，22 前景格）。产出在 `mods/sprite_demo/sprite_*.png`（16×16 + `_256.png` 放大版）和 `D:\tmp\sdv-mod-generator\outputs\mods\sprite_demo_*\*.zip`。

## Gotcha（错误签名 → 修复）

- **MiniMax 域名**：`api.minimax.io` 返回 `invalid api key`（status 2049）→ 正确是 `api.minimaxi.com`（返回 `base_resp.status_code=0`）。
- **MiniMax 返回 JPEG**（`image_base64`，`FF D8 FF E0`）非 PNG → 已由 `decode_image` 嗅探修复：JPEG magic 走 `decode_jpeg`（Pillow 懒加载），不再撞 `decode_png` 的 PNG 签名断言。
- **`GeneratorOutput.add_file` 原本标注 `dict | str`** → 放 PNG bytes 需放宽到 `dict | list | str | bytes`（packager 已支持 bytes，只改注解）。
- **texture 生成器旧语法**：`texture/__init__.py:79-90` 的 `SourceRect/ToRect` + `@/Maps/springobjects` 前缀是旧 CP 语法，现代用 `FromArea/ToArea` + 无 `@` 前缀。
- **默认 `openai` sprite provider 潜藏失效 → 已修（auto-detect）**（critic 发现）：`SPRITE_IMAGE_PROVIDER` 原先默认 `openai` → `_generate_openai_image` 用 `OPENAI_BASE_URL` + `/images/generations` + `gpt-image-1.5`，但 `OPENAI_BASE_URL` 默认（config.py:71）和 `.env` 都指向 `api.minimaxi.com/v1`（MiniMax chat 端点，只服务 M2.7，不服务 gpt-image）。**修复**：`_generate_sprite_image` 在 `SPRITE_IMAGE_PROVIDER` 未设时 auto-detect——`OPENAI_BASE_URL` 含 `minimax` 或 `MINIMAX_API_KEY` 存在 → `minimax`，否则 `openai`；显式 `SPRITE_IMAGE_PROVIDER` 仍强制覆盖。`.env.example`/`prod.env.example` 默认写成 `minimax` 并加约束说明。conftest 加隔离 `OPENAI_BASE_URL`。测试：`TestProviderAutoDetect` 4 例（minimax base URL / minimax key / openai base URL / 显式覆盖）。
- **full-pipeline 测试的 load_dotenv 顺序 flake**（critic 纠正）：`run_pipeline` 懒 import `app.config`（pipeline.py:404），`load_dotenv(override=True)` 只在 `app.config` 首次 import 时跑一次。若首次 import 发生在 full-pipeline 测试 body 内（conftest 已 delenv `OPENAI_API_KEY` 之后），`config/.env` 的 key 会被重新注入，T2 门就发真实 LLM 调用而非「No LLM provider」回退（慢/挂起）。本 host 上 `app.config` 在 collection 期已被别的模块 import，所以 T2 跳过、全套 24s 跑完；换一台 `config/.env` 带 key 的机器会中招。预先存在（3 个既有 full-pipeline 测试同享），修法（把 `app.config` 挪进 conftest 预 import、或 full-pipeline 测试 mock T2）属共享基建改动，未在 sprite 收尾里做。

## 下一步

- texture 生成器旧字段修复（`SourceRect/ToRect` → `FromArea/ToArea`，`1.29.0` → `2.0.0`）——单独一个 PR，不混入 sprite 工作。
- MiniMax provider 与真实 API 已跑通（2026-08-30，见「事实」）；剩余未验证的是「真实 MiniMax 生图出的 sprite 进真机 SMAPI load」——目前真实调用只验到 benchmark 层（16 色/53 前景格），未把真图打进 zip 再 SMAPI load（demo zip 用的是确定性样本）。真机 load 真图是下一个可选验证。
