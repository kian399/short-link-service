from app.redis_client import redis_client

CACHE_TTL = 3600  # 1小时缓存


def get_cached_link(code: str) -> str | None:
    return redis_client.get(f"link:{code}")


def set_cached_link(code: str, original_url: str):
    redis_client.setex(f"link:{code}", CACHE_TTL, original_url)


def delete_cached_link(code: str):
    redis_client.delete(f"link:{code}")
