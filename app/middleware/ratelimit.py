"""基于 Redis 的固定窗口限流。

为什么用 Lua 而不是 INCR + EXPIRE 两条命令：
两条命令分开发，中间如果进程崩溃或网络断开，会留下一个**永不过期**的计数键，
这个 key 对应的 IP / API Key 从此永久被限流。Lua 脚本在 Redis 内部原子执行，
INCR 和 EXPIRE 要么都成功要么都不执行，没有这个窗口。

限流维度：
- 跳转（GET /{code}）：按客户端 IP，默认 600 次/分钟
- 创建（POST /api/v1/links）：按 API Key，默认 60 次/分钟

为什么不用令牌桶：固定窗口实现简单、一个 key 搞定。缺点是窗口切换的瞬间可能
放过接近两倍的突发流量。短链这种场景可以接受；生产要更平滑可换滑动窗口或令牌桶。
"""

from __future__ import annotations

from fastapi import Depends, Request

from app import metrics
from app import redis_client as redis_module
from app.api.deps import verify_api_key
from app.config import settings
from app.exceptions import RateLimited

# KEYS[1]=计数键  ARGV[1]=窗口秒数  返回本次是窗口内的第几次
_LUA_INCR_EXPIRE = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

WINDOW_SECONDS = 60


def hit(key: str) -> int:
    """原子自增并返回当前窗口内的计数；首次自增时顺带设置过期时间。"""
    return int(redis_module.redis_client.eval(_LUA_INCR_EXPIRE, 1, key, WINDOW_SECONDS))


def _enforce(scope: str, identity: str, limit: int) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return

    key = f"rl:{scope}:{identity}"
    current = hit(key)
    if current <= limit:
        return

    ttl = redis_module.redis_client.ttl(key)
    retry_after = ttl if isinstance(ttl, int) and ttl > 0 else WINDOW_SECONDS
    metrics.inc("rate_limited_total", {"scope": scope})
    raise RateLimited(retry_after=retry_after)


def limit_redirect(request: Request) -> None:
    """跳转接口的依赖：按客户端 IP 限流。"""
    ip = request.client.host if request.client else "unknown"
    _enforce("redirect", ip, settings.RATE_LIMIT_REDIRECT_PER_MIN)


def limit_create(api_key: str = Depends(verify_api_key)) -> str:
    """创建接口的依赖：先验 API Key，再按 Key 限流。顺序不能反。"""
    _enforce("create", api_key, settings.RATE_LIMIT_CREATE_PER_MIN)
    return api_key
