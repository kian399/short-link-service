# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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