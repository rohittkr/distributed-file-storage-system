from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Distributed File Storage System"
    app_env: str = "development"
    debug: bool = False
    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    default_quota_bytes: int = 512 * 1024 * 1024
    max_upload_bytes: int = 500 * 1024 * 1024
    chunk_size_bytes: int = 8 * 1024 * 1024
    storage_backend: str = "local"
    local_storage_root: str = "./storage"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore"
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
