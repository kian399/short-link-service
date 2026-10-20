from datetime import datetime
from pydantic import BaseModel, HttpUrl


class LinkCreate(BaseModel):
    original_url: HttpUrl
    custom_alias: str | None = None
    expires_at: datetime | None = None


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
