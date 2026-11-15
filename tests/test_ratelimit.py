"""限流测试：用 monkeypatch 把额度调小，避免真打 600 次。"""

from app import metrics
from app.config import settings


def _create(client, headers) -> str:
    return client.post(
        "/api/v1/links",
        json={"original_url": "https://example.com/ratelimit"},
        headers=headers,
    ).json()["code"]


def test_redirect_gets_429_with_retry_after(client, auth_headers, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIRECT_PER_MIN", 3)
    code = _create(client, auth_headers)

    for _ in range(3):
        assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    resp = client.get(f"/{code}", follow_redirects=False)
    assert resp.status_code == 429
    assert resp.json()["detail"] == "请求过于频繁，请稍后重试"
    retry_after = int(resp.headers["Retry-After"])
    assert 0 < retry_after <= 60


def test_create_gets_429_by_api_key(client, auth_headers, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_CREATE_PER_MIN", 2)

    for _ in range(2):
        assert (
            client.post(
                "/api/v1/links",
                json={"original_url": "https://example.com/a"},
                headers=auth_headers,
            ).status_code
            == 201
        )

    resp = client.post(
        "/api/v1/links",
        json={"original_url": "https://example.com/b"},
        headers=auth_headers,
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_rate_limit_counts_in_redis(client, auth_headers, fake_redis, monkeypatch):
    """同一个 IP 的计数键应该按窗口累加，且带 TTL。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIRECT_PER_MIN", 100)
    code = _create(client, auth_headers)

    client.get(f"/{code}", follow_redirects=False)
    client.get(f"/{code}", follow_redirects=False)

    keys = [k for k in fake_redis.keys("rl:redirect:*")]
    assert len(keys) == 1
    assert fake_redis.get(keys[0]) == "2"
    assert fake_redis.ttl(keys[0]) > 0


def test_rate_limit_can_be_disabled(client, auth_headers, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIRECT_PER_MIN", 1)
    code = _create(client, auth_headers)

    for _ in range(5):
        assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    assert not [k for k in fake_redis.keys("rl:*")]


def test_rejection_increments_metric(client, auth_headers, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIRECT_PER_MIN", 1)
    code = _create(client, auth_headers)

    client.get(f"/{code}", follow_redirects=False)
    client.get(f"/{code}", follow_redirects=False)

    body = metrics.render()
    assert "rate_limited_total" in body
    assert 'scope="redirect"' in body
