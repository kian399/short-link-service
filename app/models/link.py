from datetime import datetime

from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, index=True, nullable=False)
    original_url = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    clicks = relationship(
        "ClickStat", back_populates="link", cascade="all, delete-orphan", passive_deletes=True
    )


class ClickStat(Base):
    __tablename__ = "click_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, ForeignKey("links.id"), nullable=False)
    clicked_at = Column(DateTime, default=datetime.utcnow)
    ip = Column(String(45))
    user_agent = Column(String(255))

    link = relationship("Link", back_populates="clicks")


class LinkStatsDaily(Base):
    """按天聚合的点击统计。

    跳转路径不再逐次写明细表，而是写 Redis 计数器，后台任务批量落到这里。
    主键 (link_id, stat_date) 让 UPSERT 天然幂等。
    """

    __tablename__ = "link_stats_daily"

    link_id = Column(Integer, ForeignKey("links.id", ondelete="CASCADE"), primary_key=True)
    stat_date = Column(Date, primary_key=True)
    clicks = Column(BigInteger, nullable=False, default=0, server_default="0")
