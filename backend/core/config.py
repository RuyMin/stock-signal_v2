"""환경설정 — pydantic-settings."""
from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class VibeEnv(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 환경 식별
    VIBE_ENV: VibeEnv = VibeEnv.DEV
    TZ: str = "Asia/Seoul"

    # PostgreSQL
    DATABASE_URL: str

    # Backend
    BACKEND_LOG_LEVEL: str = "DEBUG"
    CORS_ORIGINS: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_dev(self) -> bool:
        return self.VIBE_ENV == VibeEnv.DEV


settings = Settings()
