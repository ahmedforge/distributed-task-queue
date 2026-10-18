from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Distributed Task Queue"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "dtq_db"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    RETRY_BASE_DELAY: float = Field(default=2.0, gt=0)
    RETRY_MAX_DELAY: float = Field(default=60.0, gt=0)
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    S3_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "dtq-jobs"
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    STORAGE_DIR: str = "data/jobs"
    MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024
    MAX_PDF_PAGES: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"


settings = Settings()
