# JOURNEY.md — AgentMODGenerator

一个 AI 从一句话生成星露谷 Content Patcher 模组的项目。这份文档记录的不是「做了什么」，而是「怎么做的」：人在决定、纠正、砍掉；AI 在搭建、证伪、诚实报告。按时间从上到下读。

- **ME = 用户**（人的请求 / 决策 / 纠正 / 转向，有原话就引原话）
- **YOU = AI**（搭了什么 / 发现了什么 / 哪里错了 / 修了什么）
- 日期 `YYYY-MM-DD`。未决项滚到最上面的「风险与待办」。

---

## 风险与待办

- **P5 部署 + 监控** — AGENTS.md 仍标 TODO，容器化、健康检查、runbook 未落地。（起步 / 加固期）
- **human-mindset 架构** — 未完成；campaign 文件已作为未用 WIP 删除（2026-08-07）。线上管道仍是 Route → Generate → T1 → T2 → Package。（差口岸期）
- **`general_author` 正确性地板** — 已修 `data_schemas.json` 并加 T1 游戏数据门，但「加载成功 ≠ 数据正确」的闭环只验证了三份 LLM 包，不是一个关死的地板。（修老师期）
- **Soulbound Bloodhound** — 被提为下一参考；主要是自定义 NPC + C# DLL，超出纯 CP 边界。（边界期）
- **BroadcastAPI 级商店** — 7 月 16 日的重写没提交；后来的工作让 10 个模板「能加载」，不是对齐电视购物「看→买→次日邮寄」回路。（差口岸期）
- **Session 4 收尾** — 当时「LLM 模组进真机正确性检查」进行到一半，日志里已把该发现写成 Session 6 的触发点。（老师是错的期）
- **sprite_generator 未接管道** — `sprite` phase 没注册进 `pack/__init__.py` 和 `router.py`，走不了完整 Route → Generate → Package；真机 load 是脚本直出 zip 验证的。（贴图生成期）
- **真实生图 adapter 只接了 gpt-image（PNG）** — MiniMax `image-01` 返回 JPEG，`decode_png` 只认 PNG，未接入；接入需先做 JPEG 解码或格式转换。（贴图生成期）
- **texture 生成器的旧字段未修** — `texture/__init__.py:79-90` 用 `SourceRect/ToRect` + `Format 1.29.0`（旧语法），生产包用 `FromArea/ToArea` + `Format 2.0.0`；按精准修改原则只在新 sprite 生成器用了正确字段，没回头改 texture。（贴图生成期）

---

## 起步：P0–P4 管道跑通（2026-05 ~ 2026-07，仅 git）

早于本会话，无本地转录，只作后续的地板。

| ME | YOU |
|---|---|
| （无转录） | 搭 LangGraph 管道 Route → Generate → T1 → T2（三法官）→ Package，pack 式生成器、Discord bot、FastAPI `/v1/mods/*`、Redis 状态。 |
| | 加固：路径穿越 / `file://` 拦截、真实 Ed25519 webhook 校验、T1 逻辑反转修复、`max_t2_iterations=0` 止住无限重试、conftest 隔离 `config/.env`。 |
| | Cron / 双 agent（07-12~07-14）：移植 weather、achievements、weapon、tool 生成器，修 dispatcher（`PENDING_PICK.md` 路由、过期 FIX→KEEP）。Cron 2026-07-05 暂停。 |
| | `59464d0`（08-02）封「可加载模组」正确性地板；`mods/.test` 演示包在真机仍失败（后被 Session 2 发现）。 |

---

## 差口岸 vs 电视购物（Session 1，2026-07-16）

| ME | YOU |
|---|---|
| 要求关掉最新生成 `mods/req_57fcb5b2ff04` 与参考电视购物 Network 之间的差距 | 盘点两棵树。生成物是 13 文件的「Hello Mod」骨架（无物品图、假 i18n、非 CP 的自定义 JSON）；参考是生产 CP 包：BroadcastAPI + Esca.EMP、约 100 张物品 PNG、DynamicTokens、RealismMode Include、letterbg 邮件。 |
| 指向参考 `README.md` | 确认「看电视→买→次日邮寄」回路在**原版星露谷不可能**——需要 `Astraios.BroadcastAPI` + `Esca.EMP`。提出范围决策，被搁置。 |
| 反复问「human mindset 做完没？」 | 没。线上管道还在跑旧 T1/T2。契约注册只存在离线夹具里。Phase B/C–E、AC-01…AC-12 没开始。后续 cycle（B-RETIREMENT-01、D.049）都是 REWORK、无 GO。 |
| （隐含：继续缩小差距） | 把 `shop_channel` 生成器重写向 BroadcastAPI 生态（CustomChannels、i18n、RealismMode Include、letterbg、逐物品 PNG）。192 个受影响测试过。**没提交。** 该工作没完成 human-mindset 架构。 |

---

## 装 SMAPI + 蜂群修生成器（Session 2，2026-08-06）

六个 Agent Manager worktree：fix-shop-tokens、fix-event-npc-farm、fix-string-data、fix-gates、fix-load-test、fix-knowledge。

| ME | YOU |
|---|---|
| `install mods and test them`，目标 `D:\SteamLibrary\...\Stardew Valley` | 游戏没有 SMAPI/Mods。手动装 SMAPI 4.5.2（交互式安装器在非交互 shell 里失败），拷 Content Patcher 2.9.1、BroadcastAPI、EMP、电视购物、Hello Mod。游戏到标题画面：5 模组 + 2 content packs，零错误。 |
| 要求核对日志 | 拉 `%APPDATA%\StardewValley\ErrorLogs\SMAPI-latest.txt`，确认 `Mods loaded and ready!` 及 CP 对 Objects/CraftingRecipes/TriggerActions 的编辑。 |
| `also test .test mods` | 装全部 10 个演示包。**3/10 干净**（texture、tool_definition、weather_event）。**7/10 CP 警告**——非法 When token、对象型字符串资产、EditData+FromFile、缺 MapTiles Position、假 `UnknownNPC`。 |
| `propose fix plan or is our generator bug?` | 判定：**是生成器 bug，不是测试产物。** `.test` 与当前确定性装配器一致。四层：写死非法 CP schema；T1 只查 JSON 形状；`smapi_validate.py` 太弱；`content_actions.json` 列了 CP 2.9.1 没有的 token 且没接线。 |
| `using agent swarm to fix all steps` | 开 6 个隔离 worktree，全部合到 master（`883d46b`…`96304ee`）：Day/HasFlag token；Load + Linus NPC；管道分隔 Weapons/Achievements/Recipes；T1/静态校验 schema 检查；Windows 真 SMAPI load gate；知识 token 接进 LLM prompt。重生成演示，真 load 过，零 CP 警告。292 个修复相关测试绿。 |

---

## 重生成 .test1 + 边界 + 混合路由（Session 3，2026-08-06 → 08-07）

| ME | YOU |
|---|---|
| 用修好的格式在 `mods/.test1` 下重生成，再进真机测 | 写 `scripts/demo_generate.py`。第一次真 load **漏了一个真失败**：`farm_expansion` `Error preloading content pack`，因为 `MapTiles[].Position` 是字符串 `"60 20"`（CP 2.x 要 `{"X","Y"}` + `SetIndex`/`SetTilesheet`）。修生成器 + T1 + 静态校验 + load 解析器。重生成，SMAPI load **过（10/10）**，306 测试。 |
| 接受未提交变更的审查 | 审查发现 `demo_generate.py` 的 `--phase` 在校验名字前就清空了整个 `.test1` 树。修：拼写错误先退出、不碰树；`--phase` 只重生成那一阶段。 |
| `diff between .test1 and .test what matter why better` | 语义表：Day/HasFlag、caret/pipe 字符串、Position 对象、Linus 非 UnknownNPC、Load 非 EditData+FromFile。`.test` 7/10 警告 + 1 预加载失败；`.test1` 10/10 干净。 |
| 问 `sdv-mod-generator/` 下几十个文件为何未跟踪 | 不是 gitignore。human-mindset campaign 禁止在无 overseer GO 时提交；工作从未到 GO。`demo_generate.py` 是本会话新增。 |
| 没用的就删，说清为什么没用 | 删约 30 MB / 2000+ 文件（vendored CP 源码、850 份 campaign 状态报告、未跟踪 `game_harness`/`shadow_*`/WIP 测试）。无已跟踪文件引用它们。全量 **1203 通过，12 跳过**。 |
| `git add modify files` 再问「该跟踪的为何未跟踪，加进去推」 | 暂存 8 个修复 + `demo_generate.py`，提交 `6ae83ad`，推 `master` 到 origin。 |
| 测生成器能力边界；brainstorm 奇葩模组 | 画 10 phase vs C#-only / 未列资产。探 22 条 prompt。Tier-1 14/14 正确可加载。Tier-2：7 条 `shop_channel` 静默误路由、雪节被劫持到 weather、`forge` 硬失败成 Minecraft。 |
| （选择修路由） | 路由：未知游戏回退 `stardew_valley`；weather 覆盖仅当无节庆词；quests/fish/monsters/machines/skills/crops 走 `no_support` 哨兵；管道 fail fast。**1216 通过。** 探针：16 能用的模组 + 6 诚实拒绝，0 静默垃圾，0 崩溃。 |
| `before commit, after fix how much better` | 正确决策 63.6% → 100%（那 22 条 prompt：16 模组 + 6 拒绝）。 |
| 担心 100% 过拟合；要稀有 prompt；愿提供 Soulbound Bloodhound 作下一参考 | 分析 Bloodhound：13 MB 自定义 NPC companion + C# DLL——多半超出纯 CP 范围。选**混合**：模板作确定性地板；新 prompt 走新 `general_author` LLM CP 作者；`no_support` 只给 C#/DLL/框架。推 `422057f..f64c1ea`。 |
| （追加：教作者真 SDV 1.6 形状） | LLM 鱼模组真 SMAPI load 失败（`Fields` 按名字 `"Fish"` 键；typed 对象资产上用 pipe 字符串）。教整数 Fields 索引 + typed 对象 vs pipe/caret 规则。把 `data_schemas.json` 嵌进系统提示（`7d665a3`、`7e143cd`）。 |

---

## LLM 模组进真机：老师是错的（Session 4，2026-08-22）

| ME | YOU |
|---|---|
| 测 human-mindset / LLM 生成的模组进星露谷，查正确性（goal-persistence 框架） | 发现三份 `LLM llm_schema_*` 包已经在游戏 Mods 目录。对照 `data_schemas.json` 静态审计，隔离它们（隐藏 Demo 包），开 SMAPI 开发者日志，跑一次干净真 load。标题画面 load 应用了 Objects/Locations/CraftingRecipes；几个声称的资产（Machines/Buffs/WeatherEvents/Fish）没出现在 apply 日志——**加载成功 ≠ 数据正确**。更深：`data_schemas.json` 和 general-author 提示被标成「已对真机验证」，其中若干条目是编造或错位（`Data/WeatherEvents` 压根不存在）。本会话把发现转成 Session 6 的触发点。 |

---

## 修老师 + T1 游戏数据门（Session 6，2026-08-23）

ME 原话（从会话转录）：`Correct the teacher, then add a T1 check that fails the three bugs we already have on disk.` 提交 `7d665a3`、`7e143cd`、`f2ab2c5`。

| ME | YOU |
|---|---|
| `write the pipeline. Correct the teacher, then add a T1 check that fails the three bugs we already have on disk.` | 修 `data_schemas.json`（承重文件）：Fish 用 wiki 0–13 字段表、删多余 token；Locations 永不 `Fields.Fish`，用 `TargetField: ["Mountain","Fish"]` + `Entries`（ItemId）追加；Buffs 用官方 1.6 对象；删 `Data/WeatherEvents`。 |
| （隐含：门要能拦住已落盘的三份坏包） | 给 `gate_t1.py` 加游戏数据检查：typed 对象资产不允许 pipe/caret 字符串、整数 `Fields` 索引、不存在资产拒收。三份 `llm_schema_1/2/3` 包在新门下**复现失败**（红），修后重生成**过门**（绿）。 |

---

## 知乎专栏：把考场写成文章（Session 7，2026-08-23）

ME 原话：`use ~\.kilo\skills\tech-design-to-zhihu\ update 知乎文章，and push`。提交 `0cd9358`…`ee7cd49`。

| ME | YOU |
|---|---|
| `use tech-design-to-zhihu update 知乎文章，and push` | 用 `tech-design-to-zhihu` skill 把「星露谷是考场」写成知乎专栏 `docs/zhihu-llm-schema/`。核心一句：**形状是真相，过门才打包**。 |
| （隐含：反复打磨，去 AI 味） | 十轮打磨（`0cd9358` 围绕「可加载 zip 成功」→ `ee70aa3` 树根/主干/分支 → `f28e776` 换 skill v1.2.1 的 TL;DR/错路/收获/PS 结构 → `2ab5238` 修比喻、删重复 is/isn't → `ee7cd49` 点明「星露谷是 agent-harness 目标持久化的考场」）。配图 9 张 PNG；`PUBLISH.md` 记明知乎 CLI 0.2 **没有发文接口**，发表只能网页编辑器。 |

---

## 本次：按 journey skill 重写本档（2026-08-29）

| ME | YOU |
|---|---|
| `update journey use journey skill`，`then push to remote` | 从 git 历史 + 旧 JOURNEY.md + `docs/zhihu-llm-schema/` 重建时间线；发现旧档是英文、缺风险前置、缺经验段。按 journey skill 改为中文、风险与待办置顶、补 Session 6/7、加下方经验段。 |

---

## 多模态调研 + sprite 贴图生成（2026-08-29）

ME 原话：`我们做那些原创性工作，让agent能像人一样创造mod？多模态搞定了吗` → `minimax生图你会吗` → `use .env` → `go`（software-dev-loop）。

| ME | YOU |
|---|---|
| 问原创性工作 + 多模态是否搞定 | 盘点：原创在「数据层」（general_author + data_schemas 说明书 + T1 门 + 混合路由），多模态**零落地**——LLM 客户端纯文本（`llm/client.py:30-35`）、texture 生成器只画 16×16 纯色占位（`texture/__init__.py:10-25`）、全库无图像/音频库。 |
| `minimax生图你会吗` | 查证 MiniMax 生图 API（context7）：`POST /v1/image_generation`、`image-01`、尺寸 [512,2048] 8 的倍数。指出硬约束：512 下限 vs 星露谷 16×16 像素画，中间必须靠后处理。 |
| `both`（实验 + 范围判断文档） | 写 `doc/multimodal-sprite-scope.md`；无 MiniMax key 时用 `.env` 的 llm-proxy 代理调 `gpt-image-1.5` 实测：**直接生成失败**（29201 色、仅 1.1% 的 16×16 块纯色），但 downsample + 量化后鱼形保留。 |
| `use .env` | 发现根 `.env` 有 `MINIMAX_API_KEY`。调 `api.minimaxi.com`（**不是** context7 文档的 `api.minimax.io`，后者返回 invalid api key）实测 `image-01`：23978 色、24.7% 纯色块、返回 **JPEG** 非 PNG。结论同 gpt——直接生成不行，后处理能救。 |
| `go`（software-dev-loop） | 按 loop 走：plan doc（`doc/sprite-generator-plan.md`，自批判 5 处后改进）→ TDD 6 个 slice 实现 `sprite_utils`（downsample/quantize/encode_png/decode_png）+ `sprite_generator`。10 个新测试绿。 |
| （隐含：量化好坏） | benchmark：真实生图 23182 色 → 量化后 **16 色**、前景格 34，verdict PASS。 |
| （隐含：真机验收） | `generate_sprite_demo.py` 直出 zip → 真 SMAPI load：Sprite Mod 被加载为 content pack、CP 应用 EditImage 补丁、零警告。全量 **1233 passed, 12 skipped**。 |
| （隐含：字段矛盾要钉死） | 用生产包 `TV Shopping Network`（`.reference_mods`）钉死 EditImage 字段：`FromArea/ToArea` + `PatchMode` + `Format 2.0.0` 是现代的，texture 生成器的 `SourceRect/ToRect` + `1.29.0` 是旧语法。新生成器用对的，旧生成器按精准修改原则不动。 |

---

## 这个项目如何教 vibe coding with AI

### 人的工作（决定、纠正、砍掉）

- **把完成标准钉死，不许 AI 自己改软。** 全程一句话「形状是真相，过门才打包」；AI 曾把「文件能打开」当完成，被「听日志，不听口头」拦住（Session 2 / 4 / 7）。
- **量化验收，不满足于「好了」。** `before commit, after fix how much better` 逼出 63.6% → 100%；`diff between .test1 and .test what matter why better` 逼出语义对照表（Session 3）。
- **一个命令砍掉死重。** `why untracked if should track add and push` 直接清掉 30 MB / 2000+ 未用文件（Session 3）。
- **对 AI 自己教的东西也要求证伪。** `Correct the teacher, then add a T1 check that fails the three bugs` —— 不满足于修 `data_schemas`，还要一道门能当场拦住已落盘的坏包（Session 6）。

### AI 的工作（仪器化、证伪、诚实报告）

- **证伪了「原版可实现」**：看电视→买→次日邮寄在原版星露谷不可能，需要 BroadcastAPI + EMP，而非模板能补（Session 1）。
- **把「真机 load」做成门**：Windows 真 SMAPI load gate + 日志解析，让「能打开」升级成「日志认账」（Session 2/3）。
- **证伪了自己教的东西**：`data_schemas.json` 标「已验证」实则编造 `Data/WeatherEvents`；三份 LLM 包全加载、全不能玩——如实报告，而不是护短（Session 4）。
- **用数字说话**：7/10 CP 警告、3/10 干净、63.6% → 100%、1203 通过——每条结论都有参照物（Session 2/3）。

### 可复用的规则

1. **形状是真相，过门才打包。** 教的人（说明书）和查的人（门）必须用同一本 `data_schemas`；写的人不能给自己放行（Session 6/7）。
2. **完成听日志，不听口头。** SMAPI load gate 是「我写好了」的照妖镜（Session 2/3）。
3. **家常菜走菜谱，新菜才请模型。** 模板做确定性地板，`general_author` 只接新句子，`no_support` 只给 C#/DLL（Session 3）。
4. **每道门要能拦住已落盘的坏包。** 修老师的同时加一道能「fail the three bugs」的检查，不是只改一个值（Session 6）。
5. **量化「修完好了多少」。** 从 63.6% 到 100% 这种数字，比「修好了」更能拦住过拟合幻觉（Session 3）。
6. **证伪比提交更有价值。** AI 报告「我自己教的是错的」，是这次项目里最有信息量的一步（Session 4）。
7. **生图模型不会自发产出严格像素画，后处理是桥不是可选。** 两个模型实测（gpt 29201 色 / MiniMax 23978 色，都远非 8 色），但 downsample + 量化后形状保留——判断「换模型也一样」因此有双模型证据，不是猜。（贴图生成期）
8. **「好不好」要一个数字，不是一张图。** benchmark 里 23182 色 → 16 色、前景格 34，比「看起来像像素画」更能拦住幻觉；真机 load 日志里「Patched game code → Sprite Mod」是最终裁判。（贴图生成期）

### 一句话总结

人负责把题目钉住、拒绝改软、量化验收；AI 负责把标准仪器化、证伪自己教的东西、诚实报告——分工的界线就是「形状是真相，过门才打包」。
