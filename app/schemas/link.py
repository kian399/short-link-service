import re
from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_validator

ALIAS_PATTERN = re.compile(r"^[0-9A-Za-z_-]{4,16}$")
RESERVED_ALIASES = {
    "healthz", "docs", "redoc", "openapi.json", "metrics", "admin", "api", "static",
}


class LinkCreate(BaseModel):
    original_url: HttpUrl
    custom_alias: str | None = None
    expires_at: datetime | None = None

    @field_validator("custom_alias")
    @classmethod
    def validate_alias(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not ALIAS_PATTERN.match(value):
            raise ValueError("自定义别名必须是 4-16 位字母、数字、下划线或短横线")
        if value.lower() in RESERVED_ALIASES:
            raise ValueError("该别名是系统保留字")
        return value


class LinkResponse(BaseModel):
    code: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


class LinkStatsResponse(BaseModel):
    code: str
    total_clicks: int
    recent_clicks: list[datetime]