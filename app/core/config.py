from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Distributed Task Queue"
    DATABASE_URL: str = "postgresql+asyncpg://queue_user:queue_password@localhost:5433/queue_db"
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"

settings = Settings()