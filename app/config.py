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


settings = Settings()
