import json
from datetime import datetime, timezone

from app.redis_client import redis_client

CACHE_TTL = 3600      # 常规缓存 1 小时
MISS_TTL = 60         # 空值缓存 60 秒，防穿透
MISS_MARKER = "__MISS__"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ttl_for(expires_at: datetime | None) -> int:
    if not expires_at:
        return CACHE_TTL
    remaining = int((_as_utc(expires_at) - datetime.now(timezone.utc)).total_seconds())
    return max(1, min(CACHE_TTL, remaining))


def get_cached_link(code: str) -> dict | None:
    """返回 {"url": str|None, "expires_at": datetime|None} 或 {"miss": True}；未命中返回 None。"""
    raw = redis_client.get(f"link:{code}")
    if raw is None:
        return None
    if raw == MISS_MARKER:
        return {"miss": True}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    expires_raw = data.get("e")
    expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
    if expires_at is not None:
        expires_at = _as_utc(expires_at)
    return {"url": data.get("u"), "expires_at": expires_at}


def set_cached_link(code: str, original_url: str, expires_at: datetime | None = None) -> None:
    payload = json.dumps({
        "u": original_url,
        "e": expires_at.isoformat() if expires_at else None,
    })
    redis_client.setex(f"link:{code}", _ttl_for(expires_at), payload)


def set_missing(code: str) -> None:
    redis_client.setex(f"link:{code}", MISS_TTL, MISS_MARKER)


def delete_cached_link(code: str) -> None:
    redis_client.delete(f"link:{code}")