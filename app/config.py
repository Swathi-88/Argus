import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://aml_user:aml_password@localhost:5432/aml_db"
    )
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    FUZZY_MATCH_THRESHOLD: float = 65.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
