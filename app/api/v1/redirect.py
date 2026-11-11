from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.exceptions import LinkExpired, LinkNotFound
from app.models.link import Link
from app.services.cache import (
    delete_cached_link,
    get_cached_link,
    set_cached_link,
    set_missing,
)
from app.services.singleflight import single_flight
from app.services.stats import record_click

router = APIRouter(tags=["redirect"])


def _is_expired(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _load_from_db(code: str, db: Session) -> dict:
    """缓存未命中时的回源逻辑；被 singleflight 包住，同一 code 只有一个线程进来。

    注意这里不再写点击：回源只是读路径，埋点统一在 302 之前做，
    这样缓存命中的请求也能被算进统计。
    """
    link = db.query(Link).filter(Link.code == code).first()
    if link is None:
        set_missing(code)
        return {"miss": True}
    if _is_expired(link.expires_at):
        # 不缓存过期链接，直接每次回源（过期是少数路径）
        return {"url": link.original_url, "expires_at": link.expires_at}

    set_cached_link(code, link.original_url, link.expires_at)
    return {"url": link.original_url, "expires_at": link.expires_at}


@router.get("/{code}")
def redirect_to_original(
    code: str,
    db: Session = Depends(get_db),
):
    cached = get_cached_link(code)
    if cached is None:
        # 击穿保护：同一短码的并发请求只放一个去查库
        cached = single_flight.do(f"link:{code}", lambda: _load_from_db(code, db))

    if cached.get("miss"):
        raise LinkNotFound()
    if _is_expired(cached.get("expires_at")):
        delete_cached_link(code)
        raise LinkExpired()

    # 埋点放在最终返回之前：只做一次 Redis 自增，不阻塞跳转
    record_click(code)

    return RedirectResponse(url=cached["url"], status_code=302)
