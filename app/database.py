from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError
from app.config import settings

def get_engine():
    try:
        # Try connecting to configured DATABASE_URL (e.g. PostgreSQL)
        eng = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        # Test connection
        with eng.connect() as conn:
            pass
        print(f"[Database] Successfully connected to {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
        return eng
    except Exception as e:
        print(f"[Database] Warning: Could not connect to primary database ({e}). Falling back to local SQLite database 'aml_db.db'...")
        fallback_url = "sqlite:///aml_db.db"
        return create_engine(fallback_url, connect_args={"check_same_thread": False})

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
