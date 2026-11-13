# short-link-service

> 一个能扛压测、能被缓存救活的 URL 短链服务。
> **FastAPI · PostgreSQL · Redis · SQLAlchemy 2 · Alembic · Docker · pytest**

![ci](https://github.com/kian399/short-link-service/actions/workflows/ci.yml/badge.svg)

## 功能

| 能力 | 说明 |
|---|---|
| 创建短链 | 随机 7 位 Base62 短码，62^7 ≈ 3.5 万亿组合 |
| 自定义别名 | 4–16 位字母/数字/下划线/短横线，含保留字黑名单 |
| 跳转 | 302 重定向，读路径走 Redis 缓存 |
| 过期 | 创建时指定 `expires_at`，过期返回 410 |
| 鉴权 | `X-API-Key` 请求头，缺失或错误返回 401 |
| 删除 | 级联删除该短链的点击记录，并清理缓存 |
| 可观测 | structlog JSON 日志 + `X-Request-ID` 全链路透传 |
| 健康检查 | `GET /healthz` |

## 快速开始

```bash
git clone https://github.com/kian399/short-link-service.git
cd short-link-service
cp .env.example .env          # 按需修改 API_KEY
docker compose up -d --build  # postgres + redis + app，迁移自动执行
curl http://127.0.0.1:8000/healthz
```

创建一条短链：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/links \
  -H "X-API-Key: dev-secret-key-123456" \
  -H "Content-Type: application/json" \
  -d '{"original_url":"https://example.com/a/long/path"}'
```

## API

| 方法 | 路径 | 说明 | 状态码 |
|---|---|---|---|
| POST | `/api/v1/links` | 创建短链 | 201 / 401 / 409 / 422 |
| GET | `/api/v1/links/{code}` | 查询元信息 | 200 / 401 / 404 |
| DELETE | `/api/v1/links/{code}` | 删除 | 204 / 401 / 404 |
| GET | `/{code}` | 跳转 | 302 / 404 / 410 |
| GET | `/healthz` | 健康检查 | 200 |

请求示例：

```json
{ "original_url": "https://example.com/long/path",
  "custom_alias": "my-blog",
  "expires_at": "2026-12-31T00:00:00" }
```

响应示例：

```json
{ "code": "my-blog",
  "original_url": "https://example.com/long/path",
  "created_at": "2026-10-24T07:47:56",
  "expires_at": null,
  "short_url": "http://127.0.0.1:8000/my-blog" }
```

## 架构

```
             ┌──────────────┐
  GET /code  │   FastAPI    │
 ──────────► │              │
             └──────┬───────┘
                    │ 1. GET link:{code}
             ┌──────▼───────┐  命中 ──► 302
             │    Redis     │
             └──────┬───────┘
                    │ 未命中
             ┌──────▼───────┐
             │  PostgreSQL  │ ──► 回填缓存 ──► 302
             └──────────────┘
```

## 目录结构

```
short-link-service/
├── app/
│   ├── main.py              # FastAPI 实例、异常处理、路由注册
│   ├── config.py            # pydantic-settings 配置
│   ├── db.py                # engine / session 依赖
│   ├── redis_client.py      # Redis 连接
│   ├── logging_config.py    # structlog 配置
│   ├── middleware/          # Request-ID 中间件
│   ├── models/              # ORM 模型
│   ├── schemas/             # 请求/响应模型
│   ├── services/            # 短码生成、缓存
│   └── api/v1/              # 创建/查询/删除、跳转
├── alembic/                 # 数据库迁移
├── tests/                   # 单元 + 接口 + 缓存测试
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## 本地开发

```bash
uv sync                                   # 安装依赖（含 dev）
docker compose up -d postgres redis       # 只起依赖
uv run alembic upgrade head               # 建表
uv run uvicorn app.main:app --reload      # 启动
```

## 测试

```bash
uv run pytest --cov=app --cov-report=term-missing
```

当前：**52 个用例，覆盖率 90%**。测试用事务回滚隔离数据，Redis 用 fakeredis。

## 压测

环境：AMD Ryzen 7 7745HX（8C16T）/ 16GB / Windows 11，PostgreSQL 与 Redis 跑在 Docker 里，
应用单进程 `uvicorn --workers 1`。数据集 10 万条短链，80% 请求打 5% 的热点码（模拟真实流量分布）。

locust 200 并发、持续 3 分钟：

| 组 | 配置 | QPS | P50 | P95 | P99 | 错误率 |
|---|---|---|---|---|---|---|
| A | 纯数据库，无缓存 | 631 | 310ms | 370ms | 400ms | 0% |
| B | + Redis（L2） | 773 | 250ms | 300ms | 320ms | 0% |
| C | + 进程内 L1 | 804 | 240ms | 290ms | 310ms | 0% |
| D | + singleflight | 678 | 280ms | 350ms | 370ms | 0% |
| E | + 埋点旁路 | 635 | 300ms | 380ms | 410ms | 0% |
| C×4 | C 组配置 + 4 worker | 1538 | 67ms | 110ms | 130ms | 0% |

三条能站得住的结论：

1. **Redis 是这批改动里唯一的大头**：QPS 631 → 773（+22%），P99 400ms → 320ms。
2. **第二道瓶颈是单进程 CPU**：C 组 4 worker 后 QPS 直接翻到 1538，说明缓存不是万能的，
   加完缓存还得看进程模型。A 组曾经的瓶颈是连接池（默认 5 个连接，200 并发下打满）。
3. **D/E 相对 C 的小幅下降不单独归因**：三组之间 5%–16% 的差异与同配置重复测试的波动幅度相当，
   把噪声当结论是压测最常见的错误。

完整方法论、逐组归因、以及这份数据的可信度边界见 [docs/benchmark.md](docs/benchmark.md)。
踩过的坑记在 [docs/troubleshooting.md](docs/troubleshooting.md)。

## 关键设计

**短码为什么是随机 7 位**：62^7 ≈ 3.5 万亿，业务量级下碰撞概率可忽略；数据库唯一索引兜底，撞了就重试 3 次。相比“发号器 + Base62”，随机码不可枚举，也不需要额外发号服务。

**缓存为什么用 Cache-Aside + 写后失效**：跳转是读多写极少的场景，302 容忍秒级不一致。创建/删除先落库，再删缓存，避免“先删缓存再写库”被旧值回填。

**为什么要空值缓存**：不存在的短码会被反复查询打穿到数据库，用一个 60 秒的 `__MISS__` 占位挡住。

**为什么删除要级联**：`click_stats` 通过外键 `ON DELETE CASCADE` 跟随删除，避免出现孤儿统计行，也避免删除时因外键约束报 500。

## Roadmap

- [x] M0 核心链路跑通（2026-10-24）
- [x] M1 工程化：Docker / CI / 测试 / 结构化日志（2026-10-24）
- [x] M2 性能：L1 缓存、singleflight、locust 压测报告（2026-11-13）
- [ ] M3 功能：访问统计接口、限流中间件
- [ ] M4 交付：公网部署、架构文档、演示 GIF

## 明确不做

- 用户体系与多租户（只保留单一 API Key）
- 前端页面（只提供 `/docs` 与跳转）
- 短链点击实时大屏