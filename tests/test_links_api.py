from app.models.link import Link


def _create(client, headers, **overrides):
    payload = {"original_url": "https://example.com/a", **overrides}
    return client.post("/api/v1/links", json=payload, headers=headers)


def test_health_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_has_request_id_header(client):
    response = client.get("/healthz")
    assert response.headers.get("X-Request-ID")


def test_create_link_returns_201_with_7_char_code(client, auth_headers):
    response = _create(client, auth_headers)
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 7
    assert body["short_url"].endswith(body["code"])


def test_create_with_custom_alias(client, auth_headers):
    response = _create(client, auth_headers, custom_alias="hello-world")
    assert response.status_code == 201
    assert response.json()["code"] == "hello-world"


def test_duplicate_alias_returns_409(client, auth_headers):
    assert _create(client, auth_headers, custom_alias="dup-1").status_code == 201
    assert _create(client, auth_headers, custom_alias="dup-1").status_code == 409


def test_missing_api_key_returns_401(client):
    response = client.post("/api/v1/links", json={"original_url": "https://example.com/a"})
    assert response.status_code == 401


def test_wrong_api_key_returns_401(client):
    response = client.post(
        "/api/v1/links",
        json={"original_url": "https://example.com/a"},
        headers={"X-API-Key": "wrong"},
    )
    assert response.status_code == 401


def test_invalid_alias_returns_422(client, auth_headers):
    assert _create(client, auth_headers, custom_alias="ab").status_code == 422


def test_reserved_alias_returns_422(client, auth_headers):
    assert _create(client, auth_headers, custom_alias="healthz").status_code == 422


def test_get_link_returns_200(client, auth_headers):
    code = _create(client, auth_headers).json()["code"]
    response = client.get(f"/api/v1/links/{code}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["code"] == code


def test_get_unknown_link_returns_404(client, auth_headers):
    response = client.get("/api/v1/links/notexist", headers=auth_headers)
    assert response.status_code == 404


def test_delete_link_returns_204_then_404(client, auth_headers):
    code = _create(client, auth_headers).json()["code"]
    assert client.delete(f"/api/v1/links/{code}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/v1/links/{code}", headers=auth_headers).status_code == 404


def test_delete_with_clicks_succeeds(client, auth_headers):
    code = _create(client, auth_headers).json()["code"]
    client.get(f"/{code}", follow_redirects=False)  # 产生一条点击记录
    assert client.delete(f"/api/v1/links/{code}", headers=auth_headers).status_code == 204


def test_link_row_is_written(client, auth_headers, db_session):
    code = _create(client, auth_headers).json()["code"]
    assert db_session.query(Link).filter(Link.code == code).count() == 1


def test_list_links_paginated(client, auth_headers):
    # 开发库里可能有压测灌的历史数据，所以用相对增量断言，不写死绝对值。
    # 但第二页的条数必须按「剩余条数」算：CI 是空库（baseline = 0），
    # 3 条数据翻两页，第二页只有 1 条——之前写死 2 导致 CI 红、本地绿。
    baseline = client.get("/api/v1/links?page=1&size=1", headers=auth_headers).json()["total"]
    for i in range(3):
        _create(client, auth_headers, original_url=f"https://example.com/p{i}")

    resp = client.get("/api/v1/links?page=1&size=2", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == baseline + 3
    assert body["page"] == 1
    assert body["size"] == 2
    assert len(body["items"]) == 2

    resp2 = client.get("/api/v1/links?page=2&size=2", headers=auth_headers)
    items2 = resp2.json()["items"]
    assert len(items2) == min(2, baseline + 1)
    # 两页之间不能出现重复的短码
    assert not {item["code"] for item in body["items"]} & {item["code"] for item in items2}


def test_list_excludes_soft_deleted(client, auth_headers):
    keep = _create(client, auth_headers).json()["code"]
    gone = _create(client, auth_headers).json()["code"]
    client.delete(f"/api/v1/links/{gone}", headers=auth_headers)

    body = client.get("/api/v1/links", headers=auth_headers).json()
    codes = [item["code"] for item in body["items"]]
    assert keep in codes
    assert gone not in codes


def test_patch_updates_url_and_invalidates_cache(client, auth_headers, fake_redis):
    code = _create(client, auth_headers).json()["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 302
    assert fake_redis.get(f"link:{code}") is not None

    resp = client.patch(
        f"/api/v1/links/{code}",
        json={"original_url": "https://example.com/new-target"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["original_url"] == "https://example.com/new-target"
    # 缓存必须被删掉，否则还会跳老地址
    assert fake_redis.get(f"link:{code}") is None

    redirect = client.get(f"/{code}", follow_redirects=False)
    assert redirect.headers["location"] == "https://example.com/new-target"


def test_patch_unknown_link_returns_404(client, auth_headers):
    resp = client.patch(
        "/api/v1/links/nope9999",
        json={"original_url": "https://example.com/x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_soft_delete_keeps_row_but_blocks_redirect(client, auth_headers, db_session, fake_redis):
    code = _create(client, auth_headers).json()["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 302

    assert client.delete(f"/api/v1/links/{code}", headers=auth_headers).status_code == 204

    # 行还在，只是 is_active=false；统计数据不会随删除消失
    row = db_session.query(Link).filter(Link.code == code).one()
    assert row.is_active is False

    # 缓存被清掉，跳转变成 404
    assert fake_redis.get(f"link:{code}") is None
    assert client.get(f"/{code}", follow_redirects=False).status_code == 404
