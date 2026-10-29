import json
from datetime import UTC, datetime

from app import metrics
from app.config import settings
from app.redis_client import redis_client
from app.services.l1_cache import l1_cache

MISS_TTL = 60  # 空值缓存 60 秒，防穿透
MISS_MARKER = "__MISS__"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _ttl_for(expires_at: datetime | None) -> int:
    if not expires_at:
        return settings.CACHE_TTL
    remaining = int((_as_utc(expires_at) - datetime.now(UTC)).total_seconds())
    return max(1, min(settings.CACHE_TTL, remaining))


def get_cached_link(code: str) -> dict | None:
    """L1 -> L2 -> None。返回值：{"url","expires_at"} 或 {"miss": True}。"""
    key = f"link:{code}"

    if settings.CACHE_L1_ENABLED:
        hit = l1_cache.get(key)
        metrics.inc(
            "cache_lookups_total",
            {"layer": "l1", "result": "hit" if hit is not None else "miss"},
        )
        if hit is not None:
            return hit

    if not settings.CACHE_L2_ENABLED:
        return None

    raw = redis_client.get(key)
    metrics.inc(
        "cache_lookups_total",
        {"layer": "l2", "result": "hit" if raw is not None else "miss"},
    )
    if raw is None:
        return None

    if raw == MISS_MARKER:
        result: dict = {"miss": True}
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        expires_raw = data.get("e")
        expires_at = _as_utc(datetime.fromisoformat(expires_raw)) if expires_raw else None
        result = {"url": data.get("u"), "expires_at": expires_at}

    if settings.CACHE_L1_ENABLED:
        l1_cache.set(key, result)
    return result


def set_cached_link(code: str, original_url: str, expires_at: datetime | None = None) -> None:
    payload = json.dumps(
        {
            "u": original_url,
            "e": expires_at.isoformat() if expires_at else None,
        }
    )
    if settings.CACHE_L1_ENABLED:
        l1_cache.set(f"link:{code}", {"url": original_url, "expires_at": expires_at})
    if settings.CACHE_L2_ENABLED:
        redis_client.set(f"link:{code}", payload, ex=_ttl_for(expires_at))


def set_missing(code: str) -> None:
    if settings.CACHE_L1_ENABLED:
        l1_cache.set(f"link:{code}", {"miss": True}, ttl=MISS_TTL)
    if settings.CACHE_L2_ENABLED:
        redis_client.set(f"link:{code}", MISS_MARKER, ex=MISS_TTL)


def delete_cached_link(code: str) -> None:
    l1_cache.delete(f"link:{code}")
    redis_client.delete(f"link:{code}")
