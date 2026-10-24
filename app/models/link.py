from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db import Base


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, index=True, nullable=False)
    original_url = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    clicks = relationship("ClickStat", back_populates="link", cascade="all, delete-orphan", passive_deletes=True)


class ClickStat(Base):
    __tablename__ = "click_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, ForeignKey("links.id"), nullable=False)
    clicked_at = Column(DateTime, default=datetime.utcnow)
    ip = Column(String(45))
    user_agent = Column(String(255))

    link = relationship("Link", back_populates="clicks")
