# SDV Mod Generator — Build Phases

从零到可上线产品的完整路线图。每条任务都有优先级、完成标准、和负责人的期望分工。

---

## 阶段总览

```
Phase 0   → 本地跑通端到端（手动触发，返回 zip）
Phase 1   → 接 API 层 + 状态查询
Phase 2   → 接 Discord Bot
Phase 3   → 完善 Generator 覆盖
Phase 4   → 质量体系 + 测试
Phase 5   → 运维 + 部署 + 监控
```

---

## Phase 0 — 端到端跑通（Week 1）

**目标**：手动 curl 一个请求，看到 zip 包出来。

### P0.1 — 项目骨架 ✅ 已完成

- [x] 创建目录结构
- [x] 配置 requirements.txt
- [x] 配置 docker-compose.yml（PostgreSQL + Redis）
- [x] 写 .env.example
- [x] 写 db/init.sql

**完成标准**：`docker compose up -d` 之后 `psql` 能连上数据库。

---

### P0.2 — 存储层连接

**任务**：
- [ ] `storage/postgres.py` — SQLAlchemy async engine 连接 + session 管理
- [ ] `storage/redis.py` — Redis 连接 + pipeline 状态读写
- [ ] `storage/models/` — SQLAlchemy ORM 模型（User / ModRequest / ModOutput / ModHistory）
- [ ] `storage/s3.py` — S3 文件上传/下载封装
- [ ] `scripts/init_db.py` — 数据库初始化脚本

**完成标准**：能执行 `python scripts/init_db.py` 建好所有表。

---

### P0.3 — 完成 P1 Shop Channel 所有 Generator

**任务**：补全 `p1_shop_channel.py` 中剩余的 generator。

每个 generator 是一个类，放在 `generators/` 目录下：

| 文件 | Generator 类 | 职责 |
|---|---|---|
| `generators/p1_shop_channel.py` | `ShopItemPoolGenerator` | ✅ 已完成 |
| `generators/p1_tv_channel.py` | `TVChannelGenerator` | [ ] 补全 |
| `generators/p1_mail_system.py` | `MailSystemGenerator` | [ ] 补全 |
| `generators/p1_item_sprites.py` | `ItemSpritesGenerator` | [ ] 补全 |
| `generators/p1_ui_assets.py` | `UIAssetsGenerator` | [ ] 补全 |
| `generators/p1_catalog_preview.py` | `CatalogPreviewGenerator` | [ ] 补全 |
| `generators/p1_realism_damage.py` | `RealismDamageGenerator` | [ ] 补全 |
| `generators/p1_trigger_logic.py` | `TriggerLogicGenerator` | [ ] 补全 |
| `generators/p1_config_schema.py` | `ConfigSchemaGenerator` | [ ] 补全 |
| `generators/p1_manifest.py` | `ManifestGenerator` | [ ] 补全（所有模组都需要） |

**Generator 开发规范**：
1. 继承 `BaseGenerator`
2. 实现 `generate(inp: GeneratorInput) -> GeneratorOutput`
3. 实现 `validate_output(output: GeneratorOutput) -> list[str]`
4. 注册到 `generators/registry.py`
5. 在 `orchestrator/router.py` 添加关键词路由

**完成标准**：发送「做一个电视购物频道」prompt，生成包含完整文件结构的 zip（manifest.json + content.json + Assets/ + i18n/ + Data/）。

---

### P0.4 — 补全知识库基础数据

**任务**：
- [ ] `knowledge/data/item_ids.json` — 物品 ID 前缀速查（完整版，基于 TV Shopping Network 源码）
- [ ] `knowledge/data/game_systems.json` — SDV 游戏系统 API 摘要
- [ ] `knowledge/data/content_actions.json` — Content Patcher Action 类型 + 字段说明
- [ ] `scripts/seed_knowledge.py` — 脚本，把 JSON 数据加载到知识库

**来源**：从 SDV Wiki + SMAPI 文档 + 已分析的模组源码提取。

---

### P0.5 — 第一个端到端测试

**任务**：
- [ ] 手动跑一次完整流程：`curl POST /v1/mods/generate` → 看 zip 出来
- [ ] 验证 zip 包内容：manifest.json / content.json / i18n/default.json 都存在且格式正确
- [ ] 把测试用例写进 `tests/test_pipeline.py`

**完成标准**：
```bash
curl -X POST http://localhost:8000/v1/mods/generate \
  -d '{"user_id":"test","prompt":"做一个电视购物频道"}'

# 返回 request_id，日志显示 pipeline 完成
# zip 存在 /workspace/generated_mods/req_xxx.zip
# zip 包含 manifest.json + content.json + i18n/default.json
```

---

## Phase 1 — API 层 + 状态查询

**目标**：用户可以轮询请求状态，API 返回完整的请求历史。

### P1.1 — 完善 API 路由

**任务**：
- [ ] `GET /v1/mods/{request_id}` — 返回请求状态 + 结果（zip URL）
- [ ] `GET /v1/mods/{request_id}/files` — 返回生成文件的预览（JSON 结构）
- [ ] `GET /v1/users/me/history` — 返回用户最近的生成历史
- [ ] 错误处理：404 / 500 / 503 都有明确返回

---

### P1.2 — Redis 状态同步

**任务**：
- [ ] `orchestrator/pipeline.py` 里，每次状态变更都写入 Redis
- [ ] `GET /v1/mods/{request_id}` 先查 Redis 缓存，miss 再查 PostgreSQL
- [ ] 设置 Redis TTL = 24h

---

### P1.3 — 用户上下文记忆

**任务**：
- [ ] 每次生成完成后，写 `mod_history` 表（user_id / prompt / summary）
- [ ] `router.py` 读取用户最近 N 条历史，作为 context 传给 generator
- [ ] 支持用户说「把之前的那个购物频道加一个损坏物品功能」（多轮修正）

**完成标准**：用户连续两次对话，第二次能感知到第一次的上下文。

---

## Phase 2 — Discord Bot

**目标**：Discord 用户发消息，Bot 生成模组并推送结果。

### P2.1 — Bot 骨架

**任务**：
- [ ] `app/discord/bot.py` — discord.py 基本 bot，响应 `on_message`
- [ ] `app/discord/connector.py` — WebSocket 长连接 + 请求队列
- [ ] slash command 定义：`/mod generate` + `/mod status` + `/mod history`
- [ ] Bot 配置：guild 模式（测试）→ global 模式（上线）

---

### P2.2 — 状态推送

**任务**：
- [ ] Bot 轮询 Redis，检测到状态变更 → 主动发 Discord 消息更新用户
- [ ] 消息格式：进度条（⚙️ 生成中...）→ 完成卡片（🎉 模组已就绪！）
- [ ] 支持按钮：重新生成 / 查看文件 / 下载 zip

---

### P2.3 — 素材上传

**任务**：
- [ ] 用户上传图片附件（参考图）→ Bot 拉取 → 传给 generator
- [ ] 图片大小限制：<= 8MB，格式：PNG/JPG
- [ ] 临时 URL 存储到 OSS，24h 后自动清理

---

### P2.4 — 安装引导

**任务**：
- [ ] 生成完成后，Bot 发送安装说明（把 zip 放进 Mods 文件夹）
- [ ] 检测 SMAPI 是否安装（用户回复检测）
- [ ] 常见错误 FAQ 快捷按钮

---

## Phase 3 — 完善 Generator 覆盖

**目标**：支持用户日常会用的大部分模组类型。

### P3.1 — P0 Texture Generator

**任务**：
- [ ] `generators/p0_texture.py` — 贴图替换（最简单的模组类型）
- [ ] 用户说「把小萝卜换成红色」→ 生成贴图 + content.json
- [ ] 接入 `image_synthesize` API，生成像素风格图片

**Generator 模板**：
```python
class TextureGenerator(BaseGenerator):
    name = "gen_texture_replacement"
    phase = "p0_texture"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        # 1. 解析用户描述，确定替换哪个物品
        # 2. 调用 image_synthesize 生成像素图
        # 3. 生成 content.json EditImage patch
        # 4. 返回 files + assets
```

---

### P3.2 — NPC Generator

**任务**：
- [ ] `generators/p1_npc.py` — 添加新 NPC
- [ ] `generators/p1_npc_dialogue.py` — NPC 对话生成
- [ ] `generators/p1_npc_schedule.py` — NPC 作息表
- [ ] `generators/p1_npc_sprite.py` — NPC 像素外观生成

**技术点**：需要生成 Data/Characters 对话 + PNG sprite sheet（需要 `image_synthesize`）

---

### P3.3 — 事件 / 触发器 Generator

**任务**：
- [ ] `generators/p1_event.py` — 随机事件定义
- [ ] `generators/p1_trigger.py` — TriggerActions 逻辑

**技术点**：Content Patcher `EditData` → `Data/Events` 或 `Data/TriggerActions`

---

### P3.4 — Router 升级（LLM 路由）

**任务**：
- [ ] `router.py` 从关键词匹配升级为 LLM 路由
- [ ] LLM 接收用户 prompt + 知识库，返回 Hint
- [ ] 评估标准：准确率 > 90%（人工抽检验证）

**Prompt 设计**：
```
用户想要：「做一个每周给我送礼物的小精灵NPC」
知识库：可用 generator = [gen_npc_sprite, gen_npc_dialogue, gen_npc_schedule, gen_mail_system]
请判断：用户想要哪个功能组合？需要哪些框架依赖？执行顺序是什么？
以 JSON 格式返回 Hint。
```

---

### P3.5 — 知识库扩充

**任务**：
- [ ] 拆解 3-5 个新模组案例（见 `knowledge/cases/02-todo.md`）
- [ ] 补充 `game_systems.json` — 更完整的 SDV API 摘要
- [ ] 补充 `content_actions.json` — 所有 Content Patcher Action 字段详解

---

## Phase 4 — 质量体系 + 测试

**目标**：生成质量稳定可控，每次代码改动都能被自动化测试保护。

### P4.1 — 单元测试

**任务**：
- [ ] 每个 generator 写单元测试（mock LLM 调用，固定随机种子）
- [ ] `tests/test_generators.py` — 覆盖所有 generator
- [ ] `tests/test_router.py` — 路由关键词匹配测试
- [ ] `tests/test_quality_gate.py` — L1/L2 检验测试（已知错误样例）

**完成标准**：`pytest tests/ -v` 全部通过，覆盖率 > 70%

---

### P4.2 — 集成测试

**任务**：
- [ ] `tests/test_pipeline_integration.py` — 跑完整 pipeline，检查 zip 输出
- [ ] `tests/fixtures/` — 准备多组测试 prompt（正常 / 边界 / 错误输入）
- [ ] CI/CD 自动跑集成测试（GitHub Actions）

---

### P4.3 — L2 Gate Prompt 调优

**任务**：
- [ ] 收集 20 个历史生成样例（人工标注质量分数）
- [ ] 用样例调优 L2 prompt，提升评分准确率
- [ ] 建立 ground truth 评估集

---

### P4.4 — 错误兜底机制

**任务**：
- [ ] generator 报错 → 自动 fallback 到模板生成
- [ ] L2 不通过且 LLM 无法修正 → 人工审核队列
- [ ] 用户可反馈「结果不对」→ 触发重生成

---

## Phase 5 — 运维 + 部署 + 监控

**目标**：服务稳定，可扩展，有可观测性。

### P5.1 — 部署

**任务**：
- [ ] `Dockerfile` — 多阶段构建（build → runtime）
- [ ] `docker-compose.prod.yml` — 生产环境配置
- [ ] 部署到 Railway / Render / 云服务器
- [ ] GitHub Actions CI/CD 流程

**Dockerfile 骨架**：
```dockerfile
FROM python:3.11-slim AS builder
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### P5.2 — 监控

**任务**：
- [ ] Sentry 接入（Python 端 + Discord bot）
- [ ] 日志聚合（结构化日志 → DataDog / Loki）
- [ ] 关键指标：请求成功率 / L1-L2 通过率 / 生成耗时 / 并发数
- [ ] Discord Bot 健康检查命令：`/mod ping`

---

### P5.3 — 配额 + 计费

**任务**：
- [ ] 用户 quota 检查（Free: 5次/天，Premium: 无限制）
- [ ] Redis 计数防刷（per-user 请求频率限制）
- [ ] 积分消耗记录（mod_history 表）

---

## 按优先级排序的任务清单

### 立即要做（这周）

```
P0.2  storage/postgres.py + storage/redis.py
P0.3  补全所有 P1 generator（gen_tv_channel / gen_mail / gen_item_sprites ...）
P0.4  补全 knowledge/data/ 基础数据
P0.5  跑通第一个端到端测试
```

### 下周

```
P1.1  完善 API 路由（GET /v1/mods/{id}）
P1.2  Redis 状态同步
P2.1  Discord Bot 骨架
P3.1  P0 Texture Generator
```

### 后续

```
P3.4  Router 升级为 LLM 路由
P4.1  单元测试 + 集成测试
P5.1  部署 + CI/CD
P5.2  监控 + Sentry
```

---

## 依赖关系图

```
P0.2 storage层 ──────────→ P1.1 API完善
         │                        │
         ↓                        ↓
P0.3 generator ───────→ P3.4 router升级
         │                        │
         ↓                        ↓
P0.4 knowledge库 ─────→ P3.5 知识库扩充
         │
         ↓
P0.5 端到端测试
         │
         ├──→ P2.1 Discord Bot
         ├──→ P3.1 P0 Texture
         └──→ P4.1 测试体系
                   │
                   ↓
               P5.1 部署
```

---

## 完成标准checklist

每个 Phase 完成后，逐一检查：

| 检查项 | 标准 |
|---|---|
| 代码能运行 | `uvicorn app.main:app` 能起来 |
| 测试通过 | `pytest tests/ -v` 全部 green |
| 类型安全 | `mypy .` 无 error |
| 文档更新 | README.md / PHASES.md 已同步 |
| Discord 能用 | Bot 能响应 `/mod generate` 并推送结果 |
| 错误有日志 | 所有异常路径都有 structlog 记录 |
| 环境变量 | 所有 secrets 都在 .env 里，无 hardcode |