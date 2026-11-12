from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # 压测时 SQL 日志会写满 stdout，直接拖慢整体吞吐
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # 取连接前探活，避免拿到被 PG 断掉的死连接
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
