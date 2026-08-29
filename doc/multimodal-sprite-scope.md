# 多模态贴图生成 —— 范围判断

> 2026-08-29。回答一个问题：要把「AI 生成贴图」接进 SDV Mod Generator，哪些能用现成生图模型补，哪些必须落在 Content Patcher 的形状里。**动手前判断，未写实现代码。**

## 结论

**贴图生成能拆成两半：像素画的「内容」可以用生图模型补，「CP 形状」必须代码写死。当前多模态零落地；已实测（gpt-image-1.5 经 llm-proxy 代理）：生图模型不能直接出对齐 16×16 网格的 sprite（生成图 29201 色、仅 1.1% 的 16×16 块纯色），但 downsample 到 16×16 + 量化后鱼形轮廓清晰保留——后处理能救，这条路通。**

一句话：生图模型负责"画什么"，代码负责"画完贴到哪"。两半之间还要一道后处理（大图 → 16×16 像素画），这道后处理是代码，不是 prompt。

---

## 一、现状：多模态零落地

四个锚点，全部指向同一个结论——现在没有任何图像生成或图像理解能力：

- `sdv-mod-generator/llm/client.py:30-35`：`CompletionClient` 协议只有 `complete`（返回 str）和 `complete_with_structured_output`（返回 JSON），messages 的 content 是 `str`，无 image part、无 `image_url`。
- `sdv-mod-generator/generators/packs/stardew_valley/features/texture/__init__.py:10-25`：`_make_png` 用 stdlib zlib 画一张 16×16 纯色 RGBA PNG。这是占位图，不是 AI 生成贴图。
- `sdv-mod-generator/requirements.txt`：无 Pillow、无任何图像/音频库，只有文本 LLM SDK（anthropic/openai）。
- 资产文件搜索 `*.png/jpg/wav/ogg/mp3` 在 `sdv-mod-generator/` 下零命中。

---

## 二、拆开：生图模型能补的 vs 必须落 CP 形状的

### 生图模型能补的（内容层）

- 一张 PNG 的像素内容：物品长什么样、NPC 肖像、配色、风格。
- 风格参考：`subject_reference` 传原版 sprite 作参考（MiniMax image-01 支持图生图）。

### 必须落 CP 形状的（结构层，代码写死，模型不能猜）

| 形状 | 锚点 | 为什么不能靠模型 |
|---|---|---|
| `Target` 资产名 | `content_actions.json:40-49`（EditImage） | 游戏认的资产路径（`Maps/springobjects`、`Characters/Abigail`），编错直接缺资产 |
| 裁剪/粘贴坐标 | `content_actions.json:45-46`（`FromArea`/`ToArea`） | "裁哪块、贴哪块"是整数坐标，16px 对齐 |
| `SpriteIndex` | `data_schemas.json:52`（`Data/Objects` 的 `Texture`+`SpriteIndex`）、`:78`（`Data/BigCraftables`） | 物品在 tilesheet 里的格索引 |
| manifest 形状 | `manifest_generator.py`（`build_manifest_dict`） | `Format/UniqueID/ContentPackFor` 缺一个 SMAPI 就拒载 |
| zip 里的相对路径 | `packager.py:59-81`（`add_file` 写 bytes 进 zip） | 路径错 → 加载器找不到文件 |
| EditImage vs Load 的选择 | `data_schemas.json:9`（`FromFile` 只在 Load/EditImage 合法） | 选错 action，补丁直接被 CP 拒绝 |

### 中间地带：后处理（代码，不是 prompt）

生图模型有尺寸下限。MiniMax `image-01` 的 `width/height` 范围 **[512, 2048]，必须是 8 的倍数**（context7 查证的 API 文档）。星露谷物品 sprite 是 **16×16** 基础格。

所以模型不能直接吐 16×16 sprite，必须：生成一张大图（如 512×512）→ 缩到目标尺寸 → **量化调色板**（把抗锯齿的渐变压成有限纯色）→ **网格对齐**（每个像素压到 16px 边界）→ 才是游戏能用的 sprite。这道工序决定成败，且是确定性代码。

---

## 三、三种贴图场景的映射

| 场景 | 生图模型给什么 | 代码必须落什么 |
|---|---|---|
| **A. 替换原版贴图**（"把 Abigail 的头发换成蓝色"） | 新发型的像素画内容 | `EditImage`：`Target: Characters/Abigail` + `FromArea/ToArea` 精确坐标 + `FromFile` 指向生成的 PNG |
| **B. 新物品贴图**（"发光鱼"） | 鱼的像素画 sprite | `Data/Objects` 的 `Texture`（tilesheet 路径）+ `SpriteIndex`（格索引）+ 把 PNG 塞进 zip 的 `Assets/` + `Load` 或 `EditImage` 引用 |
| **C. 新 NPC 肖像+精灵**（最重） | 肖像 PNG + 走路动画帧 | `data_schemas.json:69`：`Load Characters/<id>` + `Portraits/<id>` 两套 sprite，多帧行走图要按 64×64 帧网格切 |

场景 C 最接近"像人一样做 mod"，也最难——不是一张图，是一套按帧网格对齐的精灵表，`subject_reference` 图生图只能帮风格，帧布局必须代码排。

---

## 四、已实测结论：直接生成不可行，后处理可行

用 `.env` 的 `OPENAI_API_KEY` + `OPENAI_BASE_URL=https://llm-proxy.tapsvc.com/v1` 代理，调 `gpt-image-1.5`（代理暴露的生图模型）实测。prompt 写死 "16x16 pixel art sprite, limited 8-color palette, no anti-aliasing"。

**直接生成 = 失败。** 生成 1024×1024 PNG，程序化分析（纯 stdlib，脚本 `sdv-mod-generator/scripts/analyze_png.py`）结果：

- `unique_colors=29201` —— 要的是 ≤8 色，实际 29201 种，差三个数量级。
- `flat_16x16_blocks=46/4096 (1.1%)` —— 只有 1.1% 的 16×16 块纯色，**没有网格对齐**。
- 背景不是纯色：四角 RGB 各不相同（249/251/252 抖动），top 色全是近白微差。

结论：生图模型把 "16x16 pixel art" 理解成"高分辨率像素风插画"，不是"严格 16×16 sprite"。直接生成这条路**证伪**。

**后处理 = 能救。** 同一张图 downsample 到 16×16、量化到 8 色（脚本 `sdv-mod-generator/scripts/downsample_png.py`），鱼形轮廓清晰保留：

```
0000000000000000
0000077777000000
0000775555750000
0000557777570000
0000755555750000
0000077700000000   ← 中间胖、两端尖，可辨认的鱼形
```

downsample 后 36 色 → 量化到 8 色后形状仍在。所以路是通的：**生图出高分辨率内容 → 程序化 downsample + 量化 + 网格对齐 → 16×16 sprite**。

退路仍成立：若对某类贴图（如需要精确调色板对齐原版 tilesheet）生图内容不够稳，用纯程序化 palette 生成兜底（现有 `_make_png` 已证明程序化能对齐）。

---

## 五、已执行的实验（可复现）

```bash
# 前提：.env 里有 OPENAI_API_KEY，代理在 OPENAI_BASE_URL=https://llm-proxy.tapsvc.com/v1
# 1. 生成（代理的 OpenAI 兼容生图端点）
#    POST /v1/images/generations  {"model":"gpt-image-1.5","prompt":"...pixel art...","size":"1024x1024","response_format":"b64_json"}
#    返回 data[0].b64_json → 解码存 PNG
# 2. 分析（纯 stdlib，无 PIL）
#    py -3 sdv-mod-generator/scripts/analyze_png.py probe.png      → 网格对齐 + 调色板
#    py -3 sdv-mod-generator/scripts/downsample_png.py probe.png   → downsample + 量化 + ASCII 轮廓
```

判断标准（三个数字钉死）：

1. `unique_colors` —— 直接生成 29201 色 → 必须量化兜底。
2. `flat_16x16_blocks` —— 1.1% → 直接生成无网格对齐。
3. downsample 后 ASCII 轮廓 —— 鱼形保留 → 后处理可行。

三个数字全部指向同一结论：**不直接生成，走后处理。**

**注意代理模型差异**：本次用 `gpt-image-1.5`（代理里没有 MiniMax 的 `image-01`，只有 `minimax/minimax-h3` 文本模型）。MiniMax `image-01` 直接调是 `POST https://api.minimax.io/v1/image_generation`（见前文 curl），未实测；但「生图模型不会自发产出严格 16×16 sprite」这个判断对任何通用生图模型性质相同，换模型大概率还是 29201 色量级 + 无网格。

---

## 六、风险

- **已实测：生图模型直接出不了严格像素画。** 必须"AI 出内容 + 程序化 downsample/量化"。原创性没丢——"画什么"仍是生图模型的，丢的只是"像素级网格对齐"这一道（本来就是代码的活）。
- **内容稳定性**：生图模型对同一条 prompt 每次结果不同（可用 `seed` 固定），且特定调色板对齐原版 tilesheet 可能不稳，需程序化 palette 兜底。
- **成本**：每次生成调外部 API，需鉴权、超时、重试、以及把 `image_base64` 落进 `add_file`（`packager.py:77-78` 已支持 bytes，不用改 packager）。
- **待确认的矛盾**：`texture/__init__.py:79-90` 用 `SourceRect/ToRect`，`content_actions.json:45-46` 写 `FromArea/ToArea`。两处字段不一致（CP 版本差异还是笔误），动手前必须先对齐，否则新贴图生成器和旧 texture 生成器会对同一件事写出两种形状。
