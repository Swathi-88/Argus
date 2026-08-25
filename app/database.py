"""
Database layer — PostgreSQL only.

Phase 4 removes the previous SQLite fallback. The audit trail's immutability
guarantee is enforced with PostgreSQL triggers and privilege revocation
(see app/db_constraints.py), and those guarantees do not exist on SQLite.
Silently degrading to a file database would mean silently degrading the
compliance guarantee, so a missing Postgres is now a hard startup failure.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings


class DatabaseUnavailableError(RuntimeError):
    """Raised when the configured PostgreSQL instance cannot be reached."""


def _redact(url: str) -> str:
    """Strips credentials out of a DSN so it is safe to log."""
    return url.split("@")[-1] if "@" in url else url


def get_engine():
    url = settings.DATABASE_URL
    if not url.startswith(("postgresql://", "postgresql+", "postgres://")):
        raise DatabaseUnavailableError(
            f"DATABASE_URL must point at PostgreSQL, got '{_redact(url)}'. "
            "The append-only audit trail requires PostgreSQL triggers."
        )

    eng = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        # Keeps long-running evaluation transactions from holding a stale socket.
        pool_recycle=1800,
    )
    try:
        with eng.connect():
            pass
    except Exception as exc:
        raise DatabaseUnavailableError(
            f"Cannot reach PostgreSQL at {_redact(url)}: {exc}\n"
            "Start it with:  docker compose up -d postgres redis"
        ) from exc

    print(f"[Database] Connected to PostgreSQL at {_redact(url)}")
    return eng


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db_schema():
    """
    Creates any missing tables, backfills columns added after the initial
    migration, then installs the append-only guarantees on audit_logs.
    """
    from sqlalchemy import inspect, text

    # Import for side effect: registers every model on Base.metadata.
    from app import models  # noqa: F401

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # --- Additive column migrations for tables created by earlier phases ---
    migrations = {
        "customers": [
            ("log_odds", "DOUBLE PRECISION"),
            ("actual_turnover", "DOUBLE PRECISION DEFAULT 0.0"),
        ],
        "audit_logs": [
            ("sequence_no", "BIGINT"),
            ("actor", "VARCHAR"),
            ("actor_role", "VARCHAR"),
            ("prev_hash", "VARCHAR"),
            ("record_hash", "VARCHAR"),
            ("customer_id", "INTEGER"),
        ],
    }

    with engine.begin() as conn:
        for table, columns in migrations.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_type in columns:
                if col_name not in present:
                    print(f"[Database] Migrating: {table}.{col_name} ({col_type})")
                    # The trigger below blocks UPDATE/DELETE on audit_logs rows,
                    # not DDL, so ALTER TABLE still succeeds.
                    conn.execute(
                        text(f'ALTER TABLE {table} ADD COLUMN "{col_name}" {col_type};')
                    )

    Base.metadata.create_all(bind=engine)

    # Install the append-only trigger + indexes last, so the table exists.
    from app.db_constraints import install_audit_immutability

    install_audit_immutability(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
