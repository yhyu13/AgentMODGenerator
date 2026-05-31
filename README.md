# SDV Mod Generator

**一句话描述**：用户说一句话，AI 自动生成 Stardew Valley 模组 zip 包。

---

## 产品目标

Discord 用户发送模组需求（如「做一个购物频道」）→ AI 自动生成完整 Content Patcher 模组 → zip 交付。

最终形态：普通玩家不需要任何 mod 开发经验，只需描述你想要什么，就能得到一个可放入游戏 Mods 文件夹的模组包。

---

## 技术架构

```
用户（Discord / Web / API）
        ↓
  FastAPI 接收请求
        ↓
  Router（知识库 Hint）
        ↓
  Orchestrator（LangGraph 流水线）
    Route → Generate → L1 Gate → L2 Gate → Package
        ↓
  S3（zip 存储） + PostgreSQL（状态/历史）
        ↓
  Discord 推送结果给用户
```

---

## 目录结构

```
sdv-mod-generator/
├── app/                   # FastAPI 应用层
│   ├── main.py           # 入口：/health, /v1/mods/generate
│   ├── api/routes.py     # API 路由（future）
│   ├── discord/          # Discord bot + connector
│   └── config.py          # 环境变量
│
├── orchestrator/         # 核心编排层
│   ├── router.py         # 意图路由（关键词 → generator 列表 + Hint）
│   ├── pipeline.py       # LangGraph 主流水线
│   ├── state.py          # PipelineState 数据结构
│   └── nodes/            # 流水线节点
│       ├── packager.py   # ZIP 打包 + S3 上传
│       └── quality_gate.py  # L1/L2 质量门（future）
│
├── generators/           # 模组生成器
│   ├── base.py          # Generator 基类
│   ├── registry.py       # 注册表（加 generator 只改这里）
│   ├── p0_texture.py     # P0：贴图替换
│   ├── p1_shop_channel.py # P1：TV购物频道
│   └── templates/        # JSON 模板片段
│
├── knowledge/            # 模组开发知识库
│   ├── sdv.py           # SDV 游戏系统映射
│   ├── generators.py     # 功能 → generator 映射
│   ├── cases/            # 模组案例拆解
│   │   ├── 01-tv-shopping-network-case.md
│   │   └── 02-todo.md
│   └── data/
│       ├── item_ids.json         # 物品/家具 ID 前缀速查
│       ├── game_systems.json    # 游戏系统 API 摘要
│       └── content_actions.json  # Content Patcher Action 类型
│
├── quality/              # 质量检验
│   ├── gate_l1.py       # L1：确定性 Schema 校验
│   └── gate_l2.py       # L2：LLM-as-Judge 二审
│
├── storage/              # 存储层
│   ├── postgres.py      # PostgreSQL 连接
│   ├── redis.py         # Redis 连接 + 状态缓存
│   ├── s3.py            # S3/OSS 文件存储
│   └── models/          # SQLAlchemy 模型
│
├── db/
│   ├── init.sql         # 建表 SQL
│   └── migrations/      # alembic 迁移（future）
│
├── tests/
│   ├── test_generators.py
│   ├── test_quality_gate.py
│   └── fixtures/        # 测试样本
│
├── scripts/
│   ├── init_db.py       # 初始化数据库
│   └── seed_knowledge.py # 填充知识库基础数据
│
├── config/
│   ├── docker-compose.yml  # PostgreSQL + Redis 本地开发
│   └── .env.example      # 环境变量模板
│
├── requirements.txt
├── Dockerfile
└── README.md            # 你在这里
```

---

## 快速启动

### 环境要求

- Python 3.11+
- Docker Desktop（本地开发用 PostgreSQL + Redis）
- OpenAI API Key（LLM 调用）
- Discord Bot Token（Discord 交互）

### 1. 克隆 + 安装依赖

```bash
git clone https://github.com/your-org/sdv-mod-generator.git
cd sdv-mod-generator
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
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
| `OPENAI_API_KEY` | LLM 生成 + L2 评审 |
| `DISCORD_BOT_TOKEN` | Discord bot 运行 |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 连接串 |
| `AWS_ACCESS_KEY_ID` | S3 文件存储（可选） |
| `AWS_SECRET_ACCESS_KEY` | S3 文件存储（可选） |

### 3. 启动依赖服务

```bash
cd config
docker compose up -d
```

这会启动：
- PostgreSQL on `localhost:5432`
- Redis on `localhost:6379`

### 4. 初始化数据库

```bash
psql $DATABASE_URL -f db/init.sql
# 或用脚本：
python scripts/init_db.py
```

### 5. 填充知识库基础数据

```bash
python scripts/seed_knowledge.py
```

### 6. 运行 API

```bash
cd sdv-mod-generator
uvicorn app.main:app --reload --port 8000
```

验证服务：

```bash
curl http://localhost:8000/health
# → {"status": "ok", "ts": "..."}
```

### 7. 触发第一个生成（测试模式）

```bash
curl -X POST http://localhost:8000/v1/mods/generate \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user_001",
    "prompt": "做一个电视购物频道，每周随机卖道具"
  }'
```

返回 `request_id` 后，在日志里看 pipeline 跑的过程：

```
INFO pipeline.routing    request_id=req_xxx phase=p1_shop_channel
INFO pipeline.generator_run request_id=req_xxx generator=gen_shop_item_pool
INFO pipeline.generation_done request_id=req_xxx files=2
INFO pipeline.done request_id=req_xxx zip_key=mods/req_xxx/req_xxx.zip
```

生成的 zip 包在 `/workspace/generated_mods/req_xxx.zip`。

---

## API 文档

### `POST /v1/mods/generate`

发起模组生成请求。

**Request**
```json
{
  "user_id": "discord_12345",
  "prompt": "做一个电视购物频道，每周随机卖道具",
  "phase": "p1_shop_channel"  // optional override
}
```

**Response**
```json
{
  "request_id": "req_a1b2c3d4e5f6",
  "status": "pending"
}
```

### `GET /v1/mods/{request_id}`

查询生成状态和结果。

**Response**
```json
{
  "request_id": "req_a1b2c3d4e5f6",
  "status": "done",
  "zip_url": "https://s3.../req_a1b2c3d4e5f6.zip",
  "files_preview": ["manifest.json", "content.json", "i18n/default.json"],
  "l1_errors": [],
  "l2_feedback": "[L2] score=8 — 完整模组，包含 TV 频道和商品系统"
}
```

---

## 流水线详解

```
用户 prompt
    ↓
┌── Step 1: Route ───────────────────────────────────────────┐
│  router.py 关键词匹配 → 返回 (phase, generator列表, hint)  │
│  hint 包含 execution_order 和 dependencies                  │
└───────────────────────────────────────────────────────────┘
    ↓
┌── Step 2: Generate ────────────────────────────────────────┐
│  每个 generator 独立跑 generate() → 返回 GeneratorOutput     │
│  顺序由 hint["execution_order"] 决定                         │
│  输出：files dict + assets list + metadata                  │
└───────────────────────────────────────────────────────────┘
    ↓
┌── Step 3: L1 Gate（快速确定性校验）────────────────────────┐
│  JSON Schema 校验 manifest.json / content.json             │
│  检查必需字段、格式、引用完整性                              │
│  失败 → 模板兜底重试                                        │
└───────────────────────────────────────────────────────────┘
    ↓
┌── Step 4: L2 Gate（LLM 评审）──────────────────────────────┐
│  第二个 LLM 跑一遍生成结果，输出质量分数                     │
│  检查逻辑连贯性、i18n 一致性、游戏机制正确性                  │
│  失败 → 返回反馈给用户，请求修正                            │
└───────────────────────────────────────────────────────────┘
    ↓
┌── Step 5: Package ─────────────────────────────────────────┐
│  files dict → ZIP → S3 上传                                 │
│  返回 zip_s3_key → 状态写入 DB                               │
└───────────────────────────────────────────────────────────┘
    ↓
Discord 推送 / API 返回结果
```

---

## Generator 开发指南

### 添加新的 Generator

**步骤 1**：继承 `BaseGenerator`，实现 `generate()` 和 `validate_output()`

```python
# generators/my_feature.py
from generators.base import BaseGenerator, GeneratorInput, GeneratorOutput

class MyFeatureGenerator(BaseGenerator):
    name = "gen_my_feature"
    phase = "p1_shop_channel"  # 或 "p0_texture"

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        out = GeneratorOutput(files={}, assets=[], metadata={})
        # 生成逻辑
        out.add_file("myfile.json", {"key": "value"})
        out.add_asset("/workspace/imgs/my_asset.png")
        return out

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        errors = []
        if "myfile.json" not in output.files:
            errors.append("gen_my_feature: missing myfile.json")
        return errors
```

**步骤 2**：注册到 `generators/registry.py`

```python
from generators.my_feature import MyFeatureGenerator
_GENERATOR_REGISTRY["gen_my_feature"] = MyFeatureGenerator
```

**步骤 3**：在 `orchestrator/router.py` 添加关键词路由

```python
FEATURE_TO_GENERATORS = {
    "my_feature": ["gen_my_feature", "gen_related_feature"],
}
FEATURE_TO_PHASE = {
    "my_feature": "p1_shop_channel",
}
```

---

## 知识库维护

知识库是让 Router 正确路由 + Generator 正确生成的根基。

### 核心文件

| 文件 | 用途 |
|---|---|
| `knowledge/cases/01-*.md` | 模组案例拆解（从源码分析生成） |
| `knowledge/data/item_ids.json` | SDV 物品/家具 ID 前缀速查 |
| `knowledge/data/game_systems.json` | 游戏系统 API 摘要 |
| `knowledge/data/content_actions.json` | Content Patcher Action 类型参考 |
| `orchestrator/router.py` | 关键词 → generator 映射（功能路由表） |

### 添加新案例

```bash
# 1. 下载模组 zip，解压到 /workspace/sdv-knowledge-base/cases/
# 2. 分析源码结构
# 3. 写一个新的 case md 文件
# 4. 更新 router.py 的 FEATURE_TO_GENERATORS
```

---

## Discord Bot

Bot 运行在独立的进程，通过 webhook 推送结果。

```bash
python -m app.discord.bot
```

Bot 支持的 slash commands：

| Command | 说明 |
|---|---|
| `/mod generate <描述>` | 发起模组生成 |
| `/mod status <request_id>` | 查询状态 |
| `/mod history` | 查看历史生成记录 |

---

## 开发规范

### 代码风格
- 类型注解必须写（`mypy` 检查）
- 所有文件级 docstring 用英文简短描述
- 日志用 structlog，字段名用 snake_case

### 测试
```bash
pytest tests/ -v
```

### 提交前检查
```bash
# 1. 类型检查
mypy sdv-mod-generator/

# 2. 格式化
ruff check .

# 3. 运行测试
pytest tests/
```

---

## 环境变量参考

完整列表见 `config/.env.example`

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI API Key |
| `DISCORD_BOT_TOKEN` | — | Discord Bot Token |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL 连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `AWS_ACCESS_KEY_ID` | — | AWS S3 Access Key |
| `AWS_SECRET_ACCESS_KEY` | — | AWS S3 Secret |
| `S3_BUCKET` | `sdv-mod-generator` | S3 Bucket 名 |
| `S3_REGION` | `us-east-1` | S3 区域 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

## 项目进度

见 `PHASES.md` — 按 Phase 描述项目从零到上线的完整路线图。

---

## License

MIT