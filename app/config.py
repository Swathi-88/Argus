import os
import secrets
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

    # --- Phase 4: Authentication & RBAC ---
    # Generated per-process when unset, which invalidates tokens on restart.
    # Set JWT_SECRET explicitly for any deployment where that matters.
    JWT_SECRET: str = os.getenv("JWT_SECRET", secrets.token_urlsafe(48))
    JWT_EXPIRY_MINUTES: int = int(os.getenv("JWT_EXPIRY_MINUTES", "480"))

    # --- Phase 4: Evaluation harness ---
    # Baseline policy: fixed-schedule periodic review, the incumbent this engine
    # is measured against.
    BASELINE_REVIEW_INTERVAL_DAYS: int = int(os.getenv("BASELINE_REVIEW_INTERVAL_DAYS", "90"))
    EVAL_NUM_CUSTOMERS: int = int(os.getenv("EVAL_NUM_CUSTOMERS", "1000"))
    EVAL_NUM_EVENTS: int = int(os.getenv("EVAL_NUM_EVENTS", "5000"))
    EVAL_HORIZON_DAYS: int = int(os.getenv("EVAL_HORIZON_DAYS", "365"))
    EVAL_RANDOM_SEED: int = int(os.getenv("EVAL_RANDOM_SEED", "42"))

    # CORS origins for the analyst console dev server.
    FRONTEND_ORIGINS: str = os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.FRONTEND_ORIGINS.split(",") if o.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

