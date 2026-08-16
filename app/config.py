import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://aml_user:aml_password@localhost:5432/aml_db"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    FUZZY_MATCH_THRESHOLD: float = 65.0
    
    # API Connector Credentials & Rate Limiting Controls
    COMPANIES_HOUSE_API_KEY: str = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")
    OPENSANCTIONS_API_KEY: str = os.getenv("OPENSANCTIONS_API_KEY", "")
    FCA_API_KEY: str = os.getenv("FCA_API_KEY", "")
    FCA_AUTH_EMAIL: str = os.getenv("FCA_AUTH_EMAIL", "compliance@aml-engine.internal")
    
    # Rate Limiting & Live API Safeguards (default false to protect API quotas)
    ENABLE_LIVE_API_CALLS: bool = os.getenv("ENABLE_LIVE_API_CALLS", "false").lower() == "true"
    MAX_LIVE_API_REQUESTS_PER_RUN: int = int(os.getenv("MAX_LIVE_API_REQUESTS_PER_RUN", "2"))


    
    # Materiality Gate Settings
    MATERIALITY_CONFIDENCE_THRESHOLD: float = 65.0
    DEDUPLICATION_WINDOW_HOURS: int = 24

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

