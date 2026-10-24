import secrets

from fastapi import Header

from app.config import settings
from app.exceptions import Unauthorized


def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    """校验 X-API-Key；缺失或不匹配统一返回 401。"""
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.API_KEY):
        raise Unauthorized()
    return x_api_key