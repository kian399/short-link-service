from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.exceptions import LinkExpired, LinkNotFound
from app.models.link import ClickStat, Link
from app.services.cache import (
    delete_cached_link,
    get_cached_link,
    set_cached_link,
    set_missing,
)

router = APIRouter(tags=["redirect"])


def _is_expired(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


@router.get("/{code}")
def redirect_to_original(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    cached = get_cached_link(code)
    if cached:
        if cached.get("miss"):
            raise LinkNotFound()
        if _is_expired(cached.get("expires_at")):
            delete_cached_link(code)
            raise LinkExpired()
        return RedirectResponse(url=cached["url"], status_code=302)

    link = db.query(Link).filter(Link.code == code).first()
    if not link:
        set_missing(code)
        raise LinkNotFound()
    if _is_expired(link.expires_at):
        raise LinkExpired()

    set_cached_link(code, link.original_url, link.expires_at)

    db.add(ClickStat(
        link_id=link.id,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255],
    ))
    db.commit()

    return RedirectResponse(url=link.original_url, status_code=302)