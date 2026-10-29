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

    # 性能相关：压测时可通过环境变量关掉某一层，做四组对比
    CACHE_L1_ENABLED: bool = True
    CACHE_L2_ENABLED: bool = True
    L1_CACHE_SIZE: int = 10000
    L1_CACHE_TTL: float = 30.0


settings = Settings()
