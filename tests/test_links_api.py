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
