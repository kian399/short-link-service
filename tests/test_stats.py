"""点击埋点旁路的测试：Redis 计数、原子落库、失败不丢数据、缓存命中也要计数。"""

from datetime import UTC, datetime

import pytest

from app.models.link import LinkStatsDaily
from app.services import stats


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _create(client, headers) -> str:
    body = client.post(
        "/api/v1/links",
        json={"original_url": "https://example.com/target"},
        headers=headers,
    ).json()
    return body["code"]


def test_record_click_accumulates(fake_redis):
    stats.record_click("abc1234")
    stats.record_click("abc1234")

    key = f"stats:abc1234:{_today()}"
    assert fake_redis.get(key) == "2"
    assert fake_redis.ttl(key) > 0


def test_snapshot_does_not_delete(fake_redis):
    stats.record_click("abc1234")

    rows = stats.snapshot_counts()
    assert rows == [(f"stats:abc1234:{_today()}", "abc1234", _today(), 1)]
    # 关键：读的时候不删，落库失败才有重试的机会
    assert fake_redis.get(f"stats:abc1234:{_today()}") == "1"


def test_ack_subtracts_only_flushed_part(fake_redis):
    stats.record_click("abc1234")
    rows = stats.snapshot_counts()

    # 落库期间又来了 2 次点击
    stats.record_click("abc1234")
    stats.record_click("abc1234")

    stats.ack_counts(rows)
    # 只扣掉已落库的 1 次，新增的 2 次必须留着
    assert fake_redis.get(f"stats:abc1234:{_today()}") == "2"


def test_ack_deletes_zero_key(fake_redis):
    stats.record_click("abc1234")
    rows = stats.snapshot_counts()
    stats.ack_counts(rows)
    assert fake_redis.get(f"stats:abc1234:{_today()}") is None


def test_snapshot_empty(fake_redis):
    assert stats.snapshot_counts() == []


def test_flush_once_upserts_daily_row(client, auth_headers, fake_redis, db_session):
    code = _create(client, auth_headers)
    stats.record_click(code)
    stats.record_click(code)

    assert stats.flush_once(db_session) == 1

    row = db_session.query(LinkStatsDaily).one()
    assert row.clicks == 2
    assert row.stat_date.strftime("%Y%m%d") == _today()


def test_flush_twice_accumulates_not_overwrites(client, auth_headers, fake_redis, db_session):
    code = _create(client, auth_headers)

    stats.record_click(code)
    stats.flush_once(db_session)
    stats.record_click(code)
    stats.record_click(code)
    stats.flush_once(db_session)

    row = db_session.query(LinkStatsDaily).one()
    assert row.clicks == 3


def test_flush_failure_keeps_counter(client, auth_headers, fake_redis, db_session, monkeypatch):
    """写库失败时计数不能丢——这是 GETDEL 方案最大的坑。"""
    code = _create(client, auth_headers)
    stats.record_click(code)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(db_session, "execute", _boom)

    with pytest.raises(RuntimeError):
        stats.flush_once(db_session)

    # 计数还在，下次还能重试
    assert fake_redis.get(f"stats:{code}:{_today()}") == "1"


def test_cache_hit_still_counts(client, auth_headers, fake_redis):
    """这是修复的核心 bug：原来命中缓存不走回源，点击永远记不上。"""
    code = _create(client, auth_headers)

    for _ in range(5):
        assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    assert fake_redis.get(f"stats:{code}:{_today()}") == "5"


def test_unknown_code_not_counted(client, fake_redis):
    assert client.get("/nope9999", follow_redirects=False).status_code == 404
    # 404 不该产生点击计数
    assert fake_redis.get(f"stats:nope9999:{_today()}") is None
