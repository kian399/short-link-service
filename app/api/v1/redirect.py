from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.link import Link
from app.services.cache import get_cached_link, set_cached_link
from app.exceptions import LinkNotFound, LinkExpired
from datetime import datetime

router = APIRouter(tags=["redirect"])


@router.get("/{code}")
def redirect_to_original(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    # 先查缓存
    cached_url = get_cached_link(code)
    if cached_url:
        return RedirectResponse(url=cached_url, status_code=302)

    # 缓存未命中，查数据库
    link = db.query(Link).filter(Link.code == code).first()
    if not link:
        raise LinkNotFound()
    if link.expires_at and link.expires_at < datetime.utcnow():
        raise LinkExpired()

    # 写入缓存
    set_cached_link(code, link.original_url)

    # 记录点击统计
    from app.models.link import ClickStat
    click = ClickStat(
        link_id=link.id,
        ip=request.client.host,
        user_agent=request.headers.get("user-agent", ""),
    )
    db.add(click)
    db.commit()

    return RedirectResponse(url=link.original_url, status_code=302)
