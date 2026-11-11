import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.redis_client as redis_module
import app.services.cache as cache_module
from app.config import settings
from app.db import Base, get_db
from app.main import app


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.DATABASE_URL, future=True)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    """每个用例包在一个最外层事务里，结束回滚，避免污染开发库。"""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def fake_redis():
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    redis_module.redis_client = client
    cache_module.redis_client = client
    yield client
    client.flushall()


@pytest.fixture()
def client(db_session, fake_redis, monkeypatch):
    # 测试里不起后台 flush 协程：异步任务会在断言之间偷偷落库，让结果不可复现
    monkeypatch.setattr(settings, "STATS_FLUSH_ENABLED", False)

    def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": settings.API_KEY}


@pytest.fixture(autouse=True)
def _reset_l1_and_metrics():
    """每个用例前后清空 L1 缓存与指标，避免相互污染。"""
    from app import metrics
    from app.services.cache import l1_cache

    l1_cache.clear()
    metrics.reset()
    yield
    l1_cache.clear()
