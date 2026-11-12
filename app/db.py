import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _split_pool() -> tuple[int, int]:
    """按 worker 数均分连接额度。

    PG 的连接上限是**全局**的（默认 100），而每个 uvicorn worker 是独立进程、
    各自建一个连接池。`--workers 4` 配 pool_size=20 + max_overflow=10 时，
    最坏能开 120 个连接，直接把 PG 顶穿：
        FATAL: sorry, too many clients already
    实测就是这么踩到的（4 worker 压测出现 18 次未捕获异常、5 个 500）。

    这里按 WEB_CONCURRENCY（uvicorn/gunicorn 都会读它）均分，并给每个进程留 1 个下限。
    """
    try:
        workers = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))
    except ValueError:
        workers = 1

    pool_size = max(1, settings.DB_POOL_SIZE // workers)
    overflow = max(0, settings.DB_MAX_OVERFLOW // workers)
    return pool_size, overflow


_pool_size, _max_overflow = _split_pool()

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # 压测时 SQL 日志会写满 stdout，直接拖慢整体吞吐
    pool_size=_pool_size,
    max_overflow=_max_overflow,
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
