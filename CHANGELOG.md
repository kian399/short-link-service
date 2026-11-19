# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-11-19

功能冻结。往后不再加接口，剩下的只有买服务器和准备面试。

### Added
- `bench/diagnose_proxy.py`：压测前体检，查 TUN / WinHTTP / 环境变量三层代理，并用两种连接池对照 loopback 延迟
- `bench/sample_system.py`：压测时旁路采样主机 CPU、应用进程 CPU、PG 活跃连接数
- `bench/make_demo_gif.py`：把真实运行记录渲染成 GIF，省掉录屏
- 1/2/4 worker 对照实验，写进 `docs/benchmark.md`
- README 增加「局限（Honest Limitations）」，把没做的事写清楚

### Changed
- 压测脚本发现环境不干净时直接退出，不再让人拿假数据写报告

## [0.5.0] - 2026-11-19

交付收尾，主要是文档和部署细节，业务代码没动。

### Added
- `docs/DEMO.md`：面试演示流程，每段都写了敲什么、看到什么、怎么说
- `docs/INTERVIEW.md`：项目问答加网络层补课
- nginx 端口改成变量（本地 8080、服务器 80），不用再手改配置
- `/metrics` 在 nginx 层只放行容器网段，公网访问返回 403
- 压测脚本加两道校验：开跑前真打一次跳转要求 302，每组跑完检查失败数和 P50，不达标直接退出

### Fixed
- README 里「生产部署」写了两遍、功能表还写着「级联删除」而代码是软删除
- nginx 等 app 改成 `condition: service_healthy`，避免启动瞬间 502
- 创建短链后没清空值缓存：先访问过不存在的 `/{code}` 再创建同名别名，60 秒内仍然 404
- 多 worker 下点击统计会被重复累加：每个 worker 都跑 flush 协程，同一批计数各写一次；现在用 Redis `SET NX EX` 抢锁
- 用例数、覆盖率更新为 72 / 90%

## [0.4.0] - 2026-11-15

### Added
- `GET /api/v1/links/{code}/stats?days=7`：按天点击统计，合并已落库的日聚合与 Redis 里还没落库的今日计数
- Redis Lua 固定窗口限流：跳转按客户端 IP 600/分钟、创建按 API Key 60/分钟，超限 429 + `Retry-After`
- 分页列表 `GET /api/v1/links`、`PATCH /api/v1/links/{code}` 改目标地址 / 过期时间，写操作统一失效缓存
- 删除改软删除（`is_active`，迁移 `0005_soft_delete`）：跳转 404，历史统计保留
- 生产编排 `docker-compose.prod.yml` + `nginx/nginx.conf` + `.env.prod.example`

### Fixed
- 统计漏记：埋点移到 302 之前，缓存命中也计数；回源不再同步写库
- 统计测试按 `link_id` 隔离，避免相互污染
- 分页用例在空库（CI）下第二页条数断言错误，CI 连红三次

## [0.3.0] - 2026-11-13

### Added
- 进程内 L1 缓存（TTLCache，10000 条 / 30s）、Redis L2、空值缓存，以及 singleflight 防击穿
- `/metrics` 手写 Prometheus 文本指标：QPS、P50/P95/P99 直方图、L1 命中率、连接池水位
- 点击埋点旁路：跳转只做一次 Redis INCR，后台每 60s 批量 UPSERT 到 `link_stats_daily`
- `bench/`：10 万条 seed、locust 80/20 热点分布、A–E 五组对比脚本与 RUNBOOK

### Changed
- 数据库连接池显式配置，并按 `WEB_CONCURRENCY` 均分，避免多 worker 顶穿 PG `max_connections`

### Docs
- `docs/benchmark.md`：五组压测报告与可信度边界；`docs/troubleshooting.md`：8 个真实踩坑

## [0.2.0] - 2026-10-24

### Added
- Dockerfile（uv 镜像构建）+ `.dockerignore`
- `docker-compose.yml` 增加 `app` 服务、postgres/redis 健康检查、启动自动迁移
- 配置项 `ENV` / `BASE_URL` / `CACHE_TTL` / `LOG_LEVEL`，响应体新增 `short_url`
- structlog JSON 日志、`X-Request-ID` 中间件、全局异常兜底
- 测试体系：conftest（事务回滚隔离 + fakeredis）、29 个用例、覆盖率 92%
- GitHub Actions：ruff + black + alembic + pytest

### Fixed
- 删除带点击记录的短链返回 500（外键改为 `ON DELETE CASCADE`）
- 11–16 位自定义别名创建返回 500（列宽 10 → 16）

## [0.1.1] - 2026-10-24

### Fixed
- 创建短链必 500：移除不存在的 `Table.count()`，改为随机 7 位短码 + 唯一索引重试
- `/healthz` 被 `/{code}` 路由遮蔽
- 缺少建表能力：接入 Alembic，新增 `0001_initial`
- 缓存命中绕过过期校验：缓存值改为 `{u, e}` 并在命中时校验
- 缺失 `X-API-Key` 返回 422 而非 401