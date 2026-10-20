from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.schemas.link import LinkCreate, LinkResponse
from app.models.link import Link
from app.services.shortener import make_code
from app.api.deps import verify_api_key
from app.exceptions import LinkNotFound, AliasConflict

router = APIRouter(prefix="/links", tags=["links"])


@router.post("", response_model=LinkResponse, status_code=201)
def create_link(
    link_data: LinkCreate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    code = link_data.custom_alias or make_code(Link.__table__.count().scalar() + 1)
    existing = db.query(Link).filter(Link.code == code).first()
    if existing:
        raise AliasConflict()
    link = Link(
        code=code,
        original_url=str(link_data.original_url),
        expires_at=link_data.expires_at,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


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
    return None
