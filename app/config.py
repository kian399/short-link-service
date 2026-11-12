from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: str = "dev"
    API_KEY: str
    DATABASE_URL: str
    REDIS_URL: str
    BASE_URL: str = "http://127.0.0.1:8000"
    CACHE_TTL: int = 3600
    LOG_LEVEL: str = "INFO"

    # 性能相关：压测时可通过环境变量逐层关掉，做五组对比
    CACHE_L1_ENABLED: bool = True
    CACHE_L2_ENABLED: bool = True
    SINGLEFLIGHT_ENABLED: bool = True
    L1_CACHE_SIZE: int = 10000
    L1_CACHE_TTL: float = 30.0

    # 点击统计：跳转只写 Redis 计数器，后台按这个间隔批量落库
    STATS_RECORD_ENABLED: bool = True
    STATS_FLUSH_ENABLED: bool = True
    STATS_FLUSH_INTERVAL: int = 60

    # 连接池：这里的数字是「每个进程」的额度，不是全应用总额度。
    # 多 worker 部署时 db.py 会按 WEB_CONCURRENCY 均分，否则 N×(pool+overflow)
    # 会顶穿 PG 的 max_connections（实测 4 worker × 30 = 120 > 100，报
    # "FATAL: sorry, too many clients already"）。
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10


settings = Settings()
