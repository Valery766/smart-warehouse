from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 3000
    API_PREFIX: str = "/api"
    JWT_SECRET: str = "change_me_dev_secret"
    JWT_EXPIRES_HOURS: int = 12
    DATABASE_URL: str = "sqlite+pysqlite:///./dev.db"
    REDIS_URL: str = "redis://redis:6379/0"
    AI_BASE_URL: str = "http://ai-service:4000"
    SOCKETIO_PATH: str = "/api/ws/dashboard"
    CORS_ALLOW_ORIGINS: str = "*"
    class Config: env_file = ".env"

@lru_cache
def get_settings() -> Settings:
    return Settings()
