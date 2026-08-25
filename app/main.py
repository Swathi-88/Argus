from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import audit
from app.auth import seed_demo_users
from app.config import settings
from app.database import DatabaseUnavailableError, SessionLocal, engine, init_db_schema
from app.db_constraints import verify_audit_immutability
from app.routers import (
    alerts,
    anomalies,
    audit as audit_router,
    auth as auth_router,
    connectors,
    customers,
    evaluation,
    events,
    materialized_events,
)
from app.seed import seed_database, seed_demo_entities


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Startup] Initializing PostgreSQL schema…")
    init_db_schema()

    db = SessionLocal()
    try:
        print("[Startup] Seeding customer base…")
        seed_database(db, target_count=2000)
        seed_demo_entities(db)

        print("[Startup] Provisioning RBAC accounts…")
        seed_demo_users(db)

        # Confirm the append-only guarantee is actually in force before serving,
        # rather than assuming the DDL took. A prototype that claims immutability
        # it does not have is worse than one that admits the gap.
        proof = verify_audit_immutability(engine)
        if proof["enforced"]:
            print("[Startup] audit_logs immutability verified — UPDATE and DELETE both refused.")
        else:
            print(
                "[Startup] WARNING: audit_logs is NOT immutable "
                f"(update_blocked={proof['update_blocked']}, delete_blocked={proof['delete_blocked']})"
            )

        audit.record(
            db,
            entity_type="SYSTEM",
            entity_id=0,
            action="SYSTEM_STARTUP",
            details={
                "version": "4.0.0",
                "audit_immutability_enforced": proof["enforced"],
                "triggers": proof["triggers_present"],
                "environment": settings.ENVIRONMENT,
            },
        )
    finally:
        db.close()

    yield
    print("[Shutdown] Cleaning up application resources…")


app = FastAPI(
    title="AML/KYC Dynamic Risk Trigger Engine",
    description=(
        "Event-driven customer risk reassessment. Phase 1 entity resolution, "
        "Phase 2 signal classification and materiality gating, Phase 3 Bayesian "
        "log-odds scoring with explainable alerts, Phase 4 analyst console, "
        "append-only audit trail, JWT RBAC, and the baseline-vs-engine "
        "evaluation harness."
    ),
    version="4.0.0",
    lifespan=lifespan,
)

# Scoped to the console's origins rather than "*": the API now carries bearer
# tokens, so a permissive origin policy is no longer harmless.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(DatabaseUnavailableError)
async def database_unavailable_handler(request, exc: DatabaseUnavailableError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# Authentication first, so it reads first in the generated docs.
app.include_router(auth_router.router)
app.include_router(alerts.router)
app.include_router(customers.router)
app.include_router(events.router)
app.include_router(materialized_events.router)
app.include_router(audit_router.router)
app.include_router(evaluation.router)
app.include_router(connectors.router)
app.include_router(anomalies.router)


@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "Dynamic Risk Trigger Engine",
        "version": "4.0.0",
        "phase": "Phase 4 — analyst console, immutable audit, RBAC, evaluation harness",
        "database": "postgresql",
        "docs_url": "/docs",
    }


@app.get("/health/ready", tags=["Health"])
def readiness():
    """
    Readiness rather than liveness: reports whether the dependencies the app
    actually needs are answering, so a container orchestrator can act on it.
    """
    from sqlalchemy import text

    from app.queue import event_queue

    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"

    checks["redis"] = "fallback:in-memory" if event_queue.use_fallback else "ok"

    ready = checks["postgres"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )
