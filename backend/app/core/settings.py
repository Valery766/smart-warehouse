from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 3000
    API_PREFIX: str = "/api"
    JWT_SECRET: str = "change_me_dev_secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_HOURS: int = 12
    JWT_ISSUER: str = "smart-warehouse"
    JWT_AUDIENCE: str = "smart-warehouse-clients"
    JWT_LEEWAY_SECONDS: int = 30
    DATABASE_URL: str = "sqlite+pysqlite:///./dev.db"
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    REDIS_URL: str = "redis://redis:6379/0"
    AI_BASE_URL: str = "http://ai-service:4000"
    AI_TIMEOUT_SECONDS: float = 10.0
    SOCKETIO_PATH: str = "/api/ws/dashboard"
    CORS_ALLOW_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    AUTO_CREATE_TABLES: bool = False
    INITIALIZE_DEMO_DATA: bool = False
    DEMO_OPERATOR_EMAIL: str = "operator@example.com"
    DEMO_OPERATOR_PASSWORD: str | None = None
    LOG_LEVEL: str = "INFO"
    ROBOT_INGEST_TOKEN: str | None = None
    ROBOT_TOKEN_HEADER: str = "X-Robot-Token"

    class Config:
        env_file = ".env"

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ALLOW_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]

@lru_cache
def get_settings() -> Settings:
    return Settings()
