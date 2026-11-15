import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, computed_field, field_validator

from app.config import settings

ALIAS_PATTERN = re.compile(r"^[0-9A-Za-z_-]{4,16}$")
RESERVED_ALIASES = {
    "healthz",
    "docs",
    "redoc",
    "openapi.json",
    "metrics",
    "admin",
    "api",
    "static",
}


def _validate_alias(value: str | None) -> str | None:
    """自定义别名的唯一校验入口，创建和更新都走这里。"""
    if value is None:
        return value
    if not ALIAS_PATTERN.match(value):
        raise ValueError("自定义别名必须是 4-16 位字母、数字、下划线或短横线")
    if value.lower() in RESERVED_ALIASES:
        raise ValueError("该别名是系统保留字")
    return value


class LinkCreate(BaseModel):
    original_url: HttpUrl
    custom_alias: str | None = None
    expires_at: datetime | None = None

    @field_validator("custom_alias")
    @classmethod
    def validate_alias(cls, value: str | None) -> str | None:
        return _validate_alias(value)


class LinkUpdate(BaseModel):
    """PATCH 用：只允许改目标地址和过期时间，都可不传。"""

    original_url: HttpUrl | None = None
    expires_at: datetime | None = None


class LinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None = None
    is_active: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def short_url(self) -> str:
        return f"{settings.BASE_URL.rstrip('/')}/{self.code}"


class LinkListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[LinkResponse]


class DailyClicks(BaseModel):
    date: date
    clicks: int


class LinkStatsResponse(BaseModel):
    code: str
    total_clicks: int
    pending_today: int
    daily: list[DailyClicks]
