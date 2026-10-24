from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import verify_api_key
from app.db import get_db
from app.exceptions import AliasConflict, LinkNotFound
from app.models.link import Link
from app.schemas.link import LinkCreate, LinkResponse
from app.services.cache import delete_cached_link
from app.services.shortener import generate_code

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
    _: str = Depends(verify_api_key),
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
            raise AliasConflict()

    for _ in range(MAX_CODE_ATTEMPTS):
        try:
            return _insert(db, generate_code(), link_data)
        except IntegrityError:
            db.rollback()

    raise AliasConflict()


@router.get("/{code}", response_model=LinkResponse)
def get_link(
    code: str,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    link = db.query(Link).filter(Link.code == code).first()
    if not link:
        raise LinkNotFound()
    return link


@router.delete("/{code}", status_code=204)
def delete_link(
    code: str,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    link = db.query(Link).filter(Link.code == code).first()
    if not link:
        raise LinkNotFound()
    db.delete(link)
    db.commit()
    delete_cached_link(code)
    return None