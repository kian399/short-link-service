from app.models.link import Link


def _create(client, headers, **overrides):
    payload = {"original_url": "https://example.com/target", **overrides}
    return client.post("/api/v1/links", json=payload, headers=headers).json()


def test_redirect_returns_302(client, auth_headers):
    code = _create(client, auth_headers)["code"]
    response = client.get(f"/{code}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/target"


def test_redirect_populates_cache(client, auth_headers, fake_redis):
    code = _create(client, auth_headers)["code"]
    client.get(f"/{code}", follow_redirects=False)
    assert fake_redis.get(f"link:{code}") is not None


def test_cache_hit_survives_db_row_removal(client, auth_headers, fake_redis, db_session):
    code = _create(client, auth_headers)["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    # 直接从数据库删掉（外键级联会一并删掉 click_stats），缓存仍在 → 证明命中缓存
    db_session.query(Link).filter(Link.code == code).delete(synchronize_session=False)
    db_session.commit()

    assert client.get(f"/{code}", follow_redirects=False).status_code == 302


def test_unknown_code_returns_404_and_negative_cache(client, fake_redis):
    response = client.get("/nope1234", follow_redirects=False)
    assert response.status_code == 404
    assert fake_redis.get("link:nope1234") == "__MISS__"


def test_expired_link_returns_410(client, auth_headers):
    body = _create(client, auth_headers, expires_at="2020-01-01T00:00:00")
    response = client.get(f"/{body['code']}", follow_redirects=False)
    assert response.status_code == 410


def test_delete_invalidates_cache(client, auth_headers, fake_redis):
    code = _create(client, auth_headers)["code"]
    client.get(f"/{code}", follow_redirects=False)
    assert fake_redis.get(f"link:{code}") is not None

    client.delete(f"/api/v1/links/{code}", headers=auth_headers)
    assert fake_redis.get(f"link:{code}") is None


def test_cached_value_contains_expiry(client, auth_headers, fake_redis):
    body = _create(client, auth_headers, expires_at="2030-01-01T00:00:00")
    client.get(f"/{body['code']}", follow_redirects=False)

    import json

    raw = json.loads(fake_redis.get(f"link:{body['code']}"))
    assert raw["u"] == "https://example.com/target"
    assert raw["e"] is not None
