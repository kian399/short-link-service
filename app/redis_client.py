import redis

from app.config import settings

# 本机开发最常见的一条报错：Error 10061 connecting to localhost:6379。
# 十次有九次是 Docker Desktop 没起、或者 redis 容器没起来，不是代码问题。
# 先把 `docker compose ps` 看一眼，比翻代码快。
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
