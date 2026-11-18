# short-link-service

> 一个能扛压测、能被缓存救活的 URL 短链服务。
>   
> **FastAPI · PostgreSQL · Redis · SQLAlchemy 2 · Alembic · Docker · pytest**

![ci](https://github.com/kian399/short-link-service/actions/workflows/ci.yml/badge.svg)

## 功能

| 能力    | 说明                                       |
| ----- | ---------------------------------------- |
| 创建短链  | 随机 7 位 Base62 短码，62^7 ≈ 3.5 万亿组合         |
| 自定义别名 | 4–16 位字母/数字/下划线/短横线，含保留字黑名单              |
| 跳转    | 302 重定向，读路径走 Redis 缓存                    |
| 过期    | 创建时指定 `expires_at`，过期返回 410              |
| 鉴权    | `X-API-Key` 请求头，缺失或错误返回 401              |
| 列表与编辑 | 分页列表、`PATCH` 改目标地址/过期时间，改完自动失效缓存 |
| 软删除   | 置 `is_active=false`，跳转 404，统计数据保留 |
| 访问统计  | 按天聚合点击数，合并 Redis 未落库的今日计数 |
| 限流    | 跳转按 IP 600/分钟、创建按 API Key 60/分钟，429 + `Retry-After` |
| 删除    | 级联删除该短链的点击记录，并清理缓存                       |
| 可观测   | structlog JSON 日志 + `X-Request-ID` 全链路透传 |
| 健康检查  | `GET /healthz`                           |

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

| 方法     | 路径                             | 说明            | 状态码                             |
| ------ | ------------------------------ | ------------- | ------------------------------- |
| POST   | `/api/v1/links`                | 创建短链          | 201 / 401 / 409 / 422 / 429     |
| GET    | `/api/v1/links`                | 分页列出未删除的短链    | 200 / 401                       |
| GET    | `/api/v1/links/{code}`         | 查询元信息         | 200 / 401 / 404                 |
| PATCH  | `/api/v1/links/{code}`         | 改目标地址 / 过期时间  | 200 / 401 / 404 / 422 / 429     |
| DELETE | `/api/v1/links/{code}`         | 软删除，跳转变 404  | 204 / 401 / 404                 |
| GET    | `/api/v1/links/{code}/stats`   | 按天点击统计        | 200 / 401 / 404 / 422           |
| GET    | `/{code}`                      | 跳转            | 302 / 404 / 410 / 429           |
| GET    | `/healthz`                     | 健康检查          | 200                             |

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
             ┌────────────────┐
  GET /code  │    FastAPI     │
 ──────────► │ 限流 → L1(进程) │
             └───────┬────────┘
                     │ L1 未命中（singleflight：同码并发只放一个回源）
             ┌───────▼────────┐  命中 ──► 302
             │     Redis      │
             └───────┬────────┘
                     │ 未命中
             ┌───────▼────────┐
             │   PostgreSQL   │ ──► 回填 L2/L1 ──► 302
             └────────────────┘

  302 之前：Redis INCR 记点击（不阻塞跳转）
  后台每 60s：读计数 → UPSERT link_stats_daily → 扣减
```

## 目录结构

```
short-link-service/
├── app/
│   ├── main.py              # FastAPI 实例、异常处理、埋点落库后台任务
│   ├── config.py            # pydantic-settings 配置（缓存/限流/埋点开关）
│   ├── db.py                # engine / session 依赖（连接池按 worker 均分）
│   ├── redis_client.py      # Redis 连接
│   ├── metrics.py           # 手写 Prometheus 文本指标，/metrics 暴露
│   ├── middleware/          # Request-ID / 指标 / 限流
│   ├── models/              # ORM 模型
│   ├── schemas/             # 请求/响应模型
│   ├── services/            # 短码、缓存(L1/L2)、singleflight、统计
│   └── api/v1/              # 创建/列表/查询/编辑/删除/统计、跳转
├── alembic/                 # 数据库迁移
├── bench/                   # seed / locust / 五组压测编排 / RUNBOOK
├── docs/                    # benchmark 报告、troubleshooting
├── nginx/                   # 生产反代配置
├── tests/                   # 单元 + 接口 + 缓存 + 限流测试
├── Dockerfile
├── docker-compose.yml       # 本地：app + pg + redis
├── docker-compose.prod.yml  # 生产：只有 nginx 暴露 80
└── .github/workflows/ci.yml
```

## 本地开发

```bash
uv sync                                   # 安装依赖（含 dev）
docker compose up -d postgres redis       # 只起依赖
uv run alembic upgrade head               # 建表
uv run uvicorn app.main:app --reload      # 启动
```

## 部署

```bash
cp .env.prod.example .env        # 改 API_KEY、POSTGRES_PASSWORD、BASE_URL 为域名
docker compose -f docker-compose.prod.yml up -d --build
curl http://<服务器 IP>/healthz  # 返回 {"status":"ok"} 即成功
```

只有 nginx 暴露 80，app / postgres / redis 都在 compose 内网；app 启动时自动 `alembic upgrade head`。
反代细节在 [nginx/nginx.conf](nginx/nginx.conf)（upstream keepalive、302 不缓冲、`/healthz` 不写日志）。

## 测试

```bash
uv run pytest --cov=app --cov-report=term-missing
```

当前：**67 个用例，覆盖率 90%**。测试用事务回滚隔离数据，Redis 用 fakeredis（限流的 Lua 由 lupa 执行）。

## 压测

环境：AMD Ryzen 7 7745HX（8C16T）/ 16GB / Windows 11，PostgreSQL 与 Redis 跑在 Docker 里，
  
应用单进程 `uvicorn --workers 1`。数据集 10 万条短链，80% 请求打 5% 的热点码（模拟真实流量分布）。

locust 200 并发、持续 3 分钟（下表是 M2 收尾时的基线；M3 的限流让跳转多了一次 Redis 调用，重测后更新）：

| 组   | 配置               | QPS  | P50   | P95   | P99   | 错误率 |
| --- | ---------------- | ---- | ----- | ----- | ----- | --- |
| A   | 纯数据库，无缓存         | 631  | 310ms | 370ms | 400ms | 0%  |
| B   | + Redis（L2）      | 773  | 250ms | 300ms | 320ms | 0%  |
| C   | + 进程内 L1         | 804  | 240ms | 290ms | 310ms | 0%  |
| D   | + singleflight   | 678  | 280ms | 350ms | 370ms | 0%  |
| E   | + 埋点旁路           | 635  | 300ms | 380ms | 410ms | 0%  |
| C×4 | C 组配置 + 4 worker | 1538 | 67ms  | 110ms | 130ms | 0%  |

三条能站得住的结论：

1. **Redis 是这批改动里唯一的大头**：QPS 631 → 773（+22%），P99 400ms → 320ms。
2. **第二道瓶颈是单进程 CPU**：C 组 4 worker 后 QPS 直接翻到 1538，说明缓存不是万能的，
     
   加完缓存还得看进程模型。A 组曾经的瓶颈是连接池（默认 5 个连接，200 并发下打满）。
3. **D/E 相对 C 的小幅下降不单独归因**：三组之间 5%–16% 的差异与同配置重复测试的波动幅度相当，
     
   把噪声当结论是压测最常见的错误。

完整方法论、逐组归因、以及这份数据的可信度边界见 [docs/benchmark.md](docs/benchmark.md)。
  
踩过的坑记在 [docs/troubleshooting.md](docs/troubleshooting.md)。

## 生产部署

```bash
cp .env.prod.example .env.prod
# 改三处：API_KEY、POSTGRES_PASSWORD、BASE_URL（改成域名或公网 IP）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

只有 nginx 暴露 80，app / postgres / redis 全在内网。数据库迁移在 app 启动时自动执行。

**更新应用后必须重启 nginx**：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build app
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx
```

nginx 启动时会把 upstream 的容器名解析成 IP 并缓存，app 容器重建后 IP 变了会 502。
详见 [docs/troubleshooting.md](docs/troubleshooting.md) 第 9 条。

## 关键设计

**短码为什么是随机 7 位**：62^7 ≈ 3.5 万亿，业务量级下碰撞概率可忽略；数据库唯一索引兜底，撞了就重试 3 次。相比“发号器 + Base62”，随机码不可枚举，也不需要额外发号服务。

**缓存为什么用 Cache-Aside + 写后失效**：跳转是读多写极少的场景，302 容忍秒级不一致。创建/删除先落库，再删缓存，避免“先删缓存再写库”被旧值回填。

**为什么要空值缓存**：不存在的短码会被反复查询打穿到数据库，用一个 60 秒的 `__MISS__` 占位挡住。

**为什么删除是软删除**：把 `is_active` 置 false，跳转立刻 404，但 `link_stats_daily` 里的日聚合还留着——删链接不该顺手删掉历史数据。读路径统一带 `is_active` 过滤，写操作（PATCH / DELETE）主动删缓存，避免旧值被回填。

## Roadmap

- [x] M0 核心链路跑通（2026-10-24）
- [x] M1 工程化：Docker / CI / 测试 / 结构化日志（2026-10-24）
- [x] M2 性能：L1 缓存、singleflight、locust 压测报告（2026-11-13）
- [x] M3 功能：统计接口、限流中间件、分页 / PATCH / 软删除（2026-11-15）
- [ ] M4 交付：公网部署、M3 后回归压测、演示 GIF

## 明确不做

- 用户体系与多租户（只保留单一 API Key）
- 前端页面（只提供 `/docs` 与跳转）
- 短链点击实时大屏
