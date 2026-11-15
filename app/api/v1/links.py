from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import verify_api_key
from app.db import get_db
from app.exceptions import AliasConflict, LinkNotFound
from app.middleware.ratelimit import limit_create
from app.models.link import Link
from app.schemas.link import (
    LinkCreate,
    LinkListResponse,
    LinkResponse,
    LinkStatsResponse,
    LinkUpdate,
)
from app.services.cache import delete_cached_link
from app.services.shortener import generate_code
from app.services.stats import read_stats

router = APIRouter(prefix="/links", tags=["links"])

MAX_CODE_ATTEMPTS = 3


def _insert(db: Session, code: str, link_data: LinkCreate) -> Link:
    link = Link(
        code=code,
        original_url=str(link_data.original_url),
        expires_at=link_data.expires_at,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.post("", response_model=LinkResponse, status_code=201)
def create_link(
    link_data: LinkCreate,
    db: Session = Depends(get_db),
    _: str = Depends(limit_create),
):
    """创建短链。

    - 自定义别名：先查重给出友好的 409，再靠唯一索引兜住并发；
    - 自动生成：随机 7 位，撞码重试，最多 3 次。
    """
    if link_data.custom_alias:
        if db.query(Link).filter(Link.code == link_data.custom_alias).first():
            raise AliasConflict()
        try:
            return _insert(db, link_data.custom_alias, link_data)
        except IntegrityError:
            db.rollback()
            raise AliasConflict() from None

    for _ in range(MAX_CODE_ATTEMPTS):
        try:
            return _insert(db, generate_code(), link_data)
        except IntegrityError:
            db.rollback()

    raise AliasConflict()


@router.get("", response_model=LinkListResponse)
def list_links(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    size: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """分页列出未删除的短链，按创建时间倒序。"""
    base = db.query(Link).filter(Link.is_active.is_(True))
    total = base.count()
    items = (
        base.order_by(Link.created_at.desc(), Link.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/{code}/stats", response_model=LinkStatsResponse)
def get_link_stats(
    code: str,
    days: int = Query(7, ge=1, le=90, description="统计最近多少天"),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """按天统计点击。数据 = 已落库的日聚合 + Redis 里还没落库的今日计数。"""
    link = db.query(Link).filter(Link.code == code).first()
    if not link:
        raise LinkNotFound()
    return read_stats(db, code, days)


@router.get("/{code}", response_model=LinkResponse)
def get_link(
    code: str,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    link = db.query(Link).filter(Link.code == code, Link.is_active.is_(True)).first()
    if not link:
        raise LinkNotFound()
    return link


@router.patch("/{code}", response_model=LinkResponse)
def update_link(
    code: str,
    payload: LinkUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(limit_create),
):
    """修改目标地址或过期时间。改完必须删缓存，否则跳转还是老地址。"""
    link = db.query(Link).filter(Link.code == code, Link.is_active.is_(True)).first()
    if not link:
        raise LinkNotFound()

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return link

    if changes.get("original_url") is not None:
        link.original_url = str(changes["original_url"])
    if "expires_at" in changes:
        link.expires_at = changes["expires_at"]

    db.commit()
    db.refresh(link)
    delete_cached_link(code)
    return link


@router.delete("/{code}", status_code=204)
def delete_link(
    code: str,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """软删除：置 is_active=false，跳转按 404 处理，统计数据保留。"""
    link = db.query(Link).filter(Link.code == code, Link.is_active.is_(True)).first()
    if not link:
        raise LinkNotFound()

    link.is_active = False
    db.commit()
    delete_cached_link(code)
    return None
