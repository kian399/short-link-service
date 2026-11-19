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


# 4 worker 那次压测 /metrics 里 db_pool_checked_out 打到 29，PG 直接报
# "FATAL: sorry, too many clients already"，5 个请求 500。当时以为是并发太高，
# 其实是 pool_size 按「每进程」算的，4 个 worker 各开 30 个，加起来 120 > 100。
_pool_size, _max_overflow = _split_pool()

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # 压测时 SQL 日志会写满 stdout，直接拖慢整体吞吐
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    # Docker Desktop 重启过 PG 容器，池里留的全是死连接，每个请求都 500，
    # 日志刷 "server closed the connection unexpectedly"；加这个之后至少能自己缓过来。
    pool_pre_ping=True,
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
