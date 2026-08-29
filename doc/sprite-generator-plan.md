# Plan — sprite_generator（AI 贴图生成落地）

> software-dev-loop 产物。前置：`doc/multimodal-sprite-scope.md` 已实测——生图模型不能直接出 16×16 像素画，但「生图出内容 → 程序化 downsample + 量化 → 16×16 sprite」这条路通。本 plan 把它变成代码。

## Goal（不被缩小）

**新增一个 sprite 生成能力：把生图模型的高分辨率输出，后处理成游戏能用的 16×16 像素画 sprite，并落进 Content Patcher 形状。** 替换现在 `texture` 生成器的纯色占位（`features/texture/__init__.py:10-25` 的 `_make_png`）。

### 成功标准（审计用，不是自我声明）

1. 一个纯函数后处理：任意 W×H RGB 图 → N×N 像素画，量化后 `unique_colors ≤ 目标色板`，且每个目标格纯色。
2. 生成器集成：prompt → 生图 API → 后处理 → `add_file` PNG 进 zip → `content.json` 引用正确路径（`packager.py:77-78` 已支持 bytes，不用改 packager）。
3. 真机 SMAPI load 通过，贴图不报缺资产。
4. benchmark：量化后 `unique_colors` 从生图原图的 ~2.4 万降到 ≤16；网格对齐（块内纯色）从 1–25% 升到 100%。

### 明确不做（防止目标漂移）

- 不做 C#/DLL 代码模组（router 已 `no_support` 拒绝）。
- 不做音频、不做新 NPC 行走动画帧（那是后续 slice，本 plan 只做「单张 sprite」）。
- 不改 packager、不改路由、不改 T1 门（除非真机 load 暴露必须改的缺口）。

## Approach

生图是 I/O（adapter），后处理是纯逻辑（核心）。把两者拆开：

```
prompt → [生图 adapter: MiniMax image-01 / gpt-image-1.5] → 原始图 bytes
       → [sprite_utils: 解码 → downsample → 量化 → N×N 像素画] → PNG bytes
       → [generator: add_file + content.json 引用]
```

核心原创在 `sprite_utils`（后处理），它是纯函数，可 TDD、可 benchmark，不依赖网络。

## Seams（tdd 要求预确认的测试边界）

**唯一要测的 seam：`sprite_utils` 的两个纯函数。** 生图 adapter 是 I/O，只 mock 不测；CP 形状落地靠真机 load gate（已有）。

职责拆成两个函数（可独立换算法）：

```python
# sprite_utils.py
def downsample(
    pixels: list[tuple[int, int, int]], width: int, height: int,
    target: int = 16,
) -> list[list[tuple[int, int, int]]]:
    """把 W×H 缩到 target×target，每格取块平均色。返回 target×target 二维网格。"""

def quantize(
    grid: list[list[tuple[int, int, int]]], palette: int = 16,
) -> tuple[list[list[tuple[int, int, int, int]]], list[tuple[int, int, int, int]]]:
    """量化到 ≤palette 色，输出 RGBA。最亮簇识别为背景 → alpha=0（透明）。
    返回 (量化后网格, 色板)。"""
```

- 测试预期值来自**独立来源**（手工算好的小图 literal、已知工作例），不重算实现逻辑（防同义反复）。
- 不测内部实现（用哪种量化算法细节），只测「输入 → 尺寸/色数/形状保留/背景透明」。
- **透明背景是 CP 形状硬要求**：物品 sprite 周围必须 alpha=0，白底生图要抠掉。

## Vertical slices（一次一个测试 → 一次实现）

1. **slice 1 — `downsample` 尺寸收缩**：4×4 图 → 2×2，每格是块平均色。TDD 红绿。
2. **slice 2 — `quantize` 色板压缩**：>palette 色的网格 → ≤palette 色，最亮簇转透明（alpha=0）。
3. **slice 3 — 形状保留**：用实测 1024×1024 鱼图（存 fixture）downsample 到 16×16，量化后前景格 ≥ 量化前前景格的 50%（客观塌缩标准）。
4. **slice 4 — PNG 编码**：RGBA 像素画 → PNG bytes（扩 `_make_png` 思路，任意尺寸 + 色板 + alpha）。
5. **slice 5 — 生成器集成**：`sprite_generator` 接生图 adapter（mock）+ `downsample`/`quantize` + `add_file` + content.json `EditImage`。先对齐 `SourceRect/ToRect` vs `FromArea/ToArea` 字段矛盾（`texture/__init__.py:79-90` vs `content_actions.json:45-46`）。
6. **slice 6 — 真机 load**：mock 生图出固定 sprite，真 SMAPI load 通过（integration，无游戏环境则 skip）。

## Benchmark（量化"好坏"，对照 baseline）

| 指标 | baseline（生图原图，实测值） | 目标（后处理后） |
|---|---|---|
| `unique_colors` | 23978–29201 | ≤16 |
| 16×16 块内纯色比例 | 1.1%–24.7% | 100% |
| 形状保留（前景格） | 32–90 格 | > 0 且形状可辨认 |

benchmark 脚本复用 `scripts/analyze_png.py`（已测过 gpt 和 MiniMax 的基线）。目标不是"好看"，是这三个数字达标。

## 风险

- **JPEG vs PNG**：MiniMax 返回 JPEG，纯 stdlib 解码 JPEG 复杂。slice 4 的 PNG 编码只负责「像素画 → PNG」，解码生图原图这一步：gpt 返回 PNG（可直接解），MiniMax 返回 JPEG（需转换）。首个 slice 先假设输入已经是解码好的 `pixels` 数组（纯函数不管格式），格式解码留给 adapter 层。
- **无 Pillow**：后处理纯 stdlib 手写，PNG 解码/编码要自己写（`analyze_png.py` 已有 PNG 解码逻辑可复用）。
