"""点击统计：跳转路径只做一次 Redis 自增，后台批量落库。

为什么不在跳转里直接 INSERT（原来的写法）：
1. 缓存命中时根本不进回源逻辑 → 点击被漏记（实测 20 次点击只记 1 条）；
2. 未命中时每次跳转都要 INSERT + COMMIT，写放大严重，QPS 被数据库写拖死。

现在跳转只付出一次 pipeline（INCR + EXPIRE），毫秒级。

落库采用「读 → 写库 → 扣减」三步，而不是 GETDEL 一把取走：
GETDEL 是原子的，但一旦后续写库失败，这批点击就永久丢了
（实测踩过：表不存在时 20 次点击直接蒸发）。
扣减用 DECRBY 只减掉「已落库的那部分」，期间新增的点击不受影响。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import redis_client as redis_module

logger = structlog.get_logger(__name__)

KEY_PREFIX = "stats:"
COUNTER_TTL = 3 * 86400  # 3 天，足够后台落库；同时防止 Redis 键无限增长

# (redis_key, code, 'YYYYMMDD', clicks)
CounterRow = tuple[str, str, str, int]


def _today() -> str:
    """按 UTC 日期分桶，和数据库里的 TIMESTAMPTZ 口径一致。"""
    return datetime.now(UTC).strftime("%Y%m%d")


def record_click(code: str) -> None:
    """跳转路径上唯一的写操作。失败也不能影响跳转。"""
    key = f"{KEY_PREFIX}{code}:{_today()}"
    try:
        pipe = redis_module.redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, COUNTER_TTL)
        pipe.execute()
    except Exception:  # noqa: BLE001 统计不能拖垮主链路
        logger.warning("record_click_failed", code=code, exc_info=True)


def snapshot_counts() -> list[CounterRow]:
    """读出当前计数，但**不删除**。只有落库成功后才会扣减。"""
    out: list[CounterRow] = []
    for key in redis_module.redis_client.scan_iter(match=f"{KEY_PREFIX}*", count=500):
        raw = redis_module.redis_client.get(key)
        if raw is None:
            continue
        try:
            clicks = int(raw)
        except (TypeError, ValueError):
            logger.warning("bad_counter_value", key=key, raw=raw)
            continue
        if clicks <= 0:
            continue
        _, code, stat_date = key.split(":", 2)
        out.append((key, code, stat_date, clicks))
    return out


def ack_counts(rows: list[CounterRow]) -> None:
    """落库成功后扣减已落库的部分；扣到 0 或负数就删键。"""
    if not rows:
        return
    pipe = redis_module.redis_client.pipeline()
    for key, _code, _stat_date, clicks in rows:
        pipe.decrby(key, clicks)
    try:
        remainders = pipe.execute()
    except Exception:  # noqa: BLE001 扣减失败最多导致重复统计，不能反过来丢数据
        logger.warning("ack_counts_failed", exc_info=True)
        return

    stale = [
        row[0]
        for row, left in zip(rows, remainders)
        if isinstance(left, int) and left <= 0
    ]
    if stale:
        try:
            redis_module.redis_client.delete(*stale)
        except Exception:  # noqa: BLE001
            logger.warning("ack_delete_failed", keys=stale)


_UPSERT_SQL = text(
    """
    INSERT INTO link_stats_daily (link_id, stat_date, clicks)
    SELECT l.id, :stat_date, :clicks FROM links l WHERE l.code = :code
    ON CONFLICT (link_id, stat_date)
    DO UPDATE SET clicks = link_stats_daily.clicks + EXCLUDED.clicks
    """
)


def flush_once(db: Session) -> int:
    """把 Redis 里的计数刷进数据库，返回本次处理的短码个数。

    用一条 INSERT ... SELECT ... ON CONFLICT 同时完成「code → link_id」的转换
    和累加，避免先查再写两次往返。写库失败时抛异常，计数器原样保留等待重试。
    """
    rows = snapshot_counts()
    if not rows:
        return 0

    try:
        for _key, code, stat_date, clicks in rows:
            try:
                day = datetime.strptime(stat_date, "%Y%m%d").date()
            except ValueError:
                logger.warning("bad_stat_date", code=code, stat_date=stat_date)
                continue
            db.execute(_UPSERT_SQL, {"code": code, "stat_date": day, "clicks": clicks})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("stats_flush_failed")
        raise

    ack_counts(rows)
    logger.info("stats_flushed", codes=len(rows), clicks=sum(r[3] for r in rows))
    return len(rows)
