# SDV Mod Generator

**一句话描述**：用户说一句话，AI 自动生成 Stardew Valley 模组 zip 包，通过 Discord 直接交付。

---

## 产品目标

Discord 用户发送模组需求（如「做一个购物频道」）→ AI 自动生成完整 Content Patcher 模组 → zip 交付到用户 DM。

最终形态：普通玩家不需要任何 mod 开发经验，只需描述你想要什么，就能得到一个可放入游戏 Mods 文件夹的模组包。

---

## 技术架构

```
用户（Discord / API）
        ↓
  FastAPI 接收请求（非阻塞，返回 request_id）
        ↓
  Router（关键词路由 → phase + generators）
        ↓
  Orchestrator（LangGraph 流水线）
    Route → Generate → T1 Gate → T2 Gate（三法官）→ Package
        ↓
  本地磁盘 / S3（zip 存储）+ PostgreSQL（状态/历史）+ Redis（状态缓存）
        ↓
  Discord Notifier 轮询 Redis，完成时 DM zip 给用户
```

---

## 目录结构

```
sdv-mod-generator/
├── app/                     # FastAPI 应用层
│   ├── main.py              # 入口 + lifespan（bot 在此启动）
│   ├── config.py            # 环境变量（.env 加载 + 校验）
│   ├── api/
│   │   ├── routes.py        # /v1/mods/* 端点
│   │   └── schemas.py       # Pydantic 模型
│   └── discord/
│       ├── bot.py           # gateway bot（slash 命令 + 自由聊天）
│       ├── notifier.py      # 完成时 DM zip 的后台 watcher
│       ├── webhook.py       # HTTP interactions + Ed25519 校验
│       └── connector.py     # webhook → API 桥接
│
├── orchestrator/            # 核心编排层
│   ├── pipeline.py          # LangGraph 主流水线
│   ├── state.py             # PipelineState 数据结构
│   ├── router.py            # 意图路由（关键词 → phase + generators）
│   └── feedback_router.py   # T2 反馈 → generator 路由
│
├── generators/              # 模组生成器
│   ├── core/base.py         # BaseGenerator / GeneratorOutput
│   ├── core/manifest.py     # 共享 manifest.json 构建器
│   ├── packager.py          # ZIP 打包
│   └── packs/stardew_valley/
│       └── features/        # 每 phase 一组生成器
│           ├── shop_channel/    # TV 购物频道（11 生成器）
│           ├── npc_schedule/    # NPC 日程 + 对话
│           ├── event_mod/       # 节日事件
│           ├── custom_crafting/ # 自定义配方
│           ├── farm_expansion/  # 农场扩建
│           ├── weather_event/   # 天气事件
│           ├── achievements/    # 成就
│           ├── weapon_definition/ # 自定义武器
│           ├── tool_definition/   # 自定义工具
│           ├── texture/         # 贴图替换
│           ├── sprite/          # AI 像素画生成
│           └── general_author/  # 通用 LLM CP 作者
│
├── quality/                 # 质量检验
│   ├── gate_t1.py           # T1：确定性 Schema + 游戏数据校验
│   └── gate_t2.py           # T2：三法官 LLM 评审
│
├── storage/                 # 存储层
│   ├── postgres.py          # PostgreSQL（async SQLAlchemy）
│   ├── redis.py             # Redis 状态缓存 + 通知目标
│   ├── s3.py                # S3 / 本地文件回退
│   ├── queries.py           # DB 查询
│   └── models/              # SQLAlchemy 模型
│
├── llm/
│   └── client.py            # OpenAI/Anthropic 客户端（带回退）
│
├── tests/                   # pytest + asyncio 测试
├── scripts/                 # 开发脚本 + smoke test
├── config/
│   ├── docker-compose.yml   # PostgreSQL + Redis 本地开发
│   └── .env.example         # 环境变量模板
│
├── requirements.txt
└── Makefile
```

---

## 快速启动

### 环境要求

- Python 3.11+
- Docker Desktop（本地开发用 PostgreSQL + Redis）
- OpenAI 兼容 API Key（LLM 调用）
- Discord Bot Token（Discord 交互）

### 1. 安装依赖

```bash
cd sdv-mod-generator
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp config/.env.example config/.env
# 编辑 config/.env，填入你的 key
```

需要填写的 key：

| Key | 用途 |
|---|---|
| `OPENAI_API_KEY` | LLM 生成 + T2 评审 |
| `OPENAI_BASE_URL` | LLM API 端点（默认 MiniMax，可换 proxy） |
| `OPENAI_MODEL` | 模型名 |
| `DISCORD_BOT_TOKEN` | Discord bot 运行 |
| `DISCORD_APP_ID` | Discord 应用 ID |
| `DISCORD_SYNC_GUILD_ID` | 开发时同步 slash 命令的服务器 ID（可选） |
| `DISCORD_PUBLIC_KEY` | webhook 路径 Ed25519 校验（可选） |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 连接串 |

### 3. 启动依赖服务

```bash
cd config
docker compose up -d
```

这会启动 PostgreSQL 和 Redis。数据库表在 FastAPI lifespan 启动时自动初始化（`init_db`）。

### 4. 启动 API + Discord bot

```bash
cd sdv-mod-generator
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

Discord bot 不是独立进程——它在 FastAPI lifespan 里启动（当 `DISCORD_BOT_TOKEN` 已设置时）。启动日志出现 `discord.bot.ready` 即 bot 已上线。

### 5. 验证

```bash
curl http://localhost:8000/health
# → {"status":"ok","discord_bot_ready":true,...}
```

### 6. Makefile

```bash
make test          # 运行测试
make test-quick    # 跳过集成测试
make lint          # mypy + ruff
make run           # 启动 API 服务
```

---

## API 端点

| Endpoint | Method | 说明 |
|---|---|---|
| `/health` | GET | 健康检查（含 discord_bot_ready） |
| `/health/deep` | GET | 深度就绪检查（DB/Redis/S3/gateway） |
| `/v1/mods/generate` | POST | 发起生成（非阻塞，返回 request_id） |
| `/v1/mods/status/{id}` | GET | 从 Redis 轮询状态 |
| `/v1/mods/{id}` | GET | 完整状态（Redis → Postgres 回退） |
| `/v1/mods/download/{id}` | GET | 预签名 S3 / file:// 下载 URL |
| `/v1/mods/{id}/files` | GET | 生成文件预览 |
| `/v1/users/{id}/history` | GET | 用户历史（API key 可选） |
| `/webhooks/discord` | POST | Discord HTTP interactions webhook |

### 发起生成

```bash
curl -X POST http://localhost:8000/v1/mods/generate \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","prompt":"make a TV shopping channel that sells rare seeds every Sunday"}'
```

返回 `request_id` + `status: running`，然后轮询 `/v1/mods/status/{id}` 直到 `done`。

---

## 流水线详解

```
用户 prompt
    ↓
Step 1: Route — 关键词匹配 → (game, phase, generators, hint)
    12 个 phase：shop_channel / texture / sprite / npc_schedule / event_mod /
    custom_crafting / farm_expansion / weather_event / achievements /
    weapon_definition / tool_definition / general_author
    ↓
Step 2: Generate — 每个 generator 跑 generate() → GeneratorOutput
    ↓
Step 3: T1 Gate — 确定性校验：manifest 必填字段、content.json CP schema、
    When token 白名单、游戏数据门（typed 对象不允许 pipe 字符串等）
    ↓
Step 4: T2 Gate — 三法官 LLM 评审（GameBalance / ContentQuality /
    TechnicalCompliance），失败时按 max_t2_iterations 回 Generate 重试
    ↓
Step 5: Package — files + assets → ZIP → 本地/S3
    ↓
Discord Notifier 轮询 Redis → 完成时 DM zip 给用户
```

---

## Discord Bot

Bot 支持两种交互：

**Slash 命令**（在服务器 `/` 选择器里）：

| Command | 说明 |
|---|---|
| `/generate <prompt>` | 发起模组生成 |
| `/status <request_id>` | 查询状态 |
| `/cancel <request_id>` | 取消进行中的请求 |
| `/history` | 查看历史生成记录 |

**自由聊天**：在频道里 @bot 并描述需求（≥20 字符），bot 会自动当作模组请求处理，完成后 DM 你 zip 包。

完整的 Discord 配置指南（权限、intents、邀请链接）见 `docs/discord-bot-setup-guide.md`。

---

## 开发规范

### 代码风格
- 类型注解必须写（`mypy` 检查）
- 文件级 docstring 用英文简短描述
- 日志用 structlog，字段名用 snake_case
- 秘密全部走环境变量，不硬编码

### 测试
```bash
pytest tests/ -v
```

真机 smoke test（需要本地 Stardew Valley + SMAPI 安装）：

```bash
SDV_INSTALL_PATH="D:\SteamLibrary\steamapps\common\Stardew Valley" \
  pytest tests/test_smapi_real_load.py -v
```

### 提交前检查
```bash
make lint       # mypy + ruff
make test       # 全量测试
```

---

## 环境变量参考

完整列表见 `config/.env.example`

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | LLM API Key |
| `OPENAI_BASE_URL` | `https://api.minimaxi.com/v1` | LLM 端点 |
| `OPENAI_MODEL` | `MiniMax-M2.7` | 模型名 |
| `DISCORD_BOT_TOKEN` | — | Discord Bot Token |
| `DISCORD_APP_ID` | — | Discord 应用 ID |
| `DISCORD_SYNC_GUILD_ID` | — | 开发时同步命令的服务器 ID |
| `DATABASE_URL` | `postgresql+asyncpg://localhost:5432/sdv_mods` | PostgreSQL 连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `S3_BUCKET` | `sdv-mod-generator` | S3 Bucket 名 |
| `S3_REGION` | `us-east-1` | S3 区域 |
| `LOCAL_OUTPUT_DIR` | `/tmp/sdv-mod-generator/outputs` | 本地 zip 输出目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

## 项目进度

见 `PHASES.md` — 按 Phase 描述项目从零到上线的完整路线图。
见 `JOURNEY.md` — 人机协作的完整时间线 + vibe-coding 经验教训。

---

## License

MIT
