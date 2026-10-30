from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    SECRET_KEY: str = "dev-secret-change-me"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True

    DATABASE_URL: str = "postgresql://cicd:cicd123@localhost:5432/cicddb"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    GITHUB_WEBHOOK_SECRET: str = "dev-webhook-secret"
    DOCKER_REGISTRY: str = "docker.io"
    DOCKER_USERNAME: str = ""
    DOCKER_PASSWORD: str = ""

    DEPLOY_HOST: str = ""
    DEPLOY_USER: str = "ubuntu"
    DEPLOY_KEY_PATH: str = ""
    DEPLOY_PORT: int = 22

    MAX_LOG_LINES: int = 5000
    BUILD_TIMEOUT_SEC: int = 1800


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
