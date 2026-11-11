def record_click(code: str) -> None:
    key = f"stats:{code}:{datetime.now(UTC):%Y%m%d}"
    pipe = redis_client.pipeline()
    pipe.incr(key)          # 原子 +1
    pipe.expire(key, 3*86400)
    pipe.execute()
