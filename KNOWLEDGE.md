# KNOWLEDGE.md — AgentMODGenerator 长期记忆

> software-dev-loop 产物。新会话先读这里，避免重新发现。按「决策 / 事实 / gotcha / 下一步」四栏，数字带参照物，缺材料标待确认。

## 决策（为什么这么做）

- **多模态贴图走「生图出内容 + 程序化后处理」路线，不做"直接生成"** —— 实测两个生图模型都不能自发产出严格 16×16 像素画，但 downsample + 量化后形状保留。后处理是桥，不是可选。
- **`sprite_utils` 拆成 `downsample` / `quantize` 两个纯函数** —— 尺寸收缩和色板压缩是两个职责，拆开各自可测、可独立换算法。量化时「最亮簇合并成一个透明色」是 CP 硬要求（近白抖动 249/251/252 必须变成一个洞）。
- **新 sprite 生成器用现代 CP 字段，不回头改旧 texture 生成器** —— 精准修改原则。texture 的 `SourceRect/ToRect` + `Format 1.29.0` 是旧语法，留着待单独修。

## 事实（数字 + baseline）

- **生图模型不产严格像素画**：gpt-image-1.5 生成 1024×1024 → 29201 色、1.1% 的 16×16 块纯色；MiniMax image-01 → 23978 色、24.7% 纯色块、背景 30% 纯白。要 ≤8 色，实际差三个数量级。
- **benchmark（后处理有效性）**：真实生图 23182 色 → 量化后 16 色、前景格 34、`verdict PASS`。脚本 `scripts/benchmark_sprite.py`。
- **真机 load**：sprite demo zip 被 SMAPI 加载为 content pack，CP 应用 EditImage 补丁、零警告（日志 `Patched game code → Sprite Mod`）。全量 1233 passed, 12 skipped。
- **EditImage 字段权威锚点**：生产包 `.reference_mods/TV Shopping Network/content.json:820-854` 用 `FromArea`/`ToArea` + `PatchMode` + `Format 2.0.0`。

## Gotcha（错误签名 → 修复）

- **MiniMax 域名**：`api.minimax.io` 返回 `invalid api key`（status 2049）→ 正确是 `api.minimaxi.com`（返回 `base_resp.status_code=0`）。
- **MiniMax 返回 JPEG**（`image_base64`，`FF D8 FF E0`）非 PNG → `decode_png` 只认 PNG，需先转格式（Windows 用 System.Drawing）。
- **`GeneratorOutput.add_file` 原本标注 `dict | str`** → 放 PNG bytes 需放宽到 `dict | list | str | bytes`（packager 已支持 bytes，只改注解）。
- **texture 生成器旧语法**：`texture/__init__.py:79-90` 的 `SourceRect/ToRect` + `@/Maps/springobjects` 前缀是旧 CP 语法，现代用 `FromArea/ToArea` + 无 `@` 前缀。

## 下一步

- `sprite` phase 注册进 `generators/packs/stardew_valley/__init__.py` + `orchestrator/router.py`，让 sprite_generator 走完整 Route → Generate → Package（当前真机 load 是脚本直出 zip）。
- MiniMax JPEG 解码接入 `_generate_sprite_image`（当前只接 gpt-image 的 PNG）。
- texture 生成器旧字段修复（`SourceRect/ToRect` → `FromArea/ToArea`，`1.29.0` → `2.0.0`）——单独一个 PR，不混入 sprite 工作。
- 生图 API 的真实调用路径（`_generate_sprite_image` 的 aiohttp 分支）尚未被 pytest 覆盖（依赖网络 + key），当前靠 deterministic + mock 覆盖。
