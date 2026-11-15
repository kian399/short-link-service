"""统计接口的测试：刚落库的点击、Redis 里还没落库的点击、链接不存在。"""

from datetime import UTC, datetime

from app.services import stats


def _create(client, headers) -> str:
    body = client.post(
        "/api/v1/links",
        json={"original_url": "https://example.com/stats"},
        headers=headers,
    ).json()
    return body["code"]


def test_pending_clicks_visible_immediately(client, auth_headers, fake_redis):
    """刚点的 5 下还没落库，但接口必须立刻能查到——这是埋点旁路最容易漏的地方。"""
    code = _create(client, auth_headers)
    for _ in range(5):
        assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    body = client.get(f"/api/v1/links/{code}/stats", headers=auth_headers).json()

    assert body["code"] == code
    assert body["pending_today"] == 5
    assert body["total_clicks"] == 5


def test_daily_after_flush(client, auth_headers, fake_redis, db_session):
    """落库之后 pending 归零，daily 出现当天一行。"""
    code = _create(client, auth_headers)
    for _ in range(3):
        client.get(f"/{code}", follow_redirects=False)

    assert stats.flush_once(db_session) == 1

    body = client.get(f"/api/v1/links/{code}/stats", headers=auth_headers).json()

    assert body["pending_today"] == 0
    assert body["total_clicks"] == 3
    assert len(body["daily"]) == 1
    assert body["daily"][0]["clicks"] == 3
    assert body["daily"][0]["date"] == datetime.now(UTC).date().isoformat()


def test_daily_merges_pending_with_flushed(client, auth_headers, fake_redis, db_session):
    """落库 2 次 + 之后再点 3 次，总数应该是 5，且当天只有一行。"""
    code = _create(client, auth_headers)

    for _ in range(2):
        client.get(f"/{code}", follow_redirects=False)
    stats.flush_once(db_session)

    for _ in range(3):
        client.get(f"/{code}", follow_redirects=False)

    body = client.get(f"/api/v1/links/{code}/stats", headers=auth_headers).json()

    assert body["total_clicks"] == 5
    assert body["pending_today"] == 3
    assert len(body["daily"]) == 1
    assert body["daily"][0]["clicks"] == 5


def test_unknown_code_returns_404(client, auth_headers):
    resp = client.get("/api/v1/links/nope9999/stats", headers=auth_headers)
    assert resp.status_code == 404


def test_invalid_days_rejected(client, auth_headers):
    code = _create(client, auth_headers)
    assert client.get(f"/api/v1/links/{code}/stats?days=0", headers=auth_headers).status_code == 422
    assert (
        client.get(f"/api/v1/links/{code}/stats?days=999", headers=auth_headers).status_code == 422
    )
