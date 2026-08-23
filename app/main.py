from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base, SessionLocal, init_db_schema
from app.seed import seed_database
from app.routers import events, customers, connectors, materialized_events, alerts, anomalies



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure DB tables exist and seed database
    print("[Startup] Initializing Database Schema...")
    init_db_schema()

    
    print("[Startup] Triggering Data Seeder...")
    db = SessionLocal()
    try:
        seed_database(db, target_count=2000)
    finally:
        db.close()
        
    yield
    # Shutdown: Clean up resources if needed
    print("[Shutdown] Cleaning up application resources...")


app = FastAPI(
    title="AML/KYC Dynamic Risk Trigger Engine",
    description="Phase 3 Engine: Bayesian Log-Odds Scoring, IsolationForest Anomaly Detection, Tier Boundary Alerts & Explainable Risk APIs",
    version="3.0.0",
    lifespan=lifespan
)

# Enable CORS for browser access / API testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(events.router)
app.include_router(customers.router)
app.include_router(connectors.router)
app.include_router(materialized_events.router)
app.include_router(alerts.router)
app.include_router(anomalies.router)




@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "Dynamic Risk Trigger Engine",
        "version": "1.0.0",
        "docs_url": "/docs"
    }
