from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base, SessionLocal
from app.seed import seed_database
from app.routers import events, customers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure DB tables exist and seed database
    print("[Startup] Initializing Database Schema...")
    Base.metadata.create_all(bind=engine)
    
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
    description="Phase 1 Prototype: Multi-tier Entity Resolution & Event Monitoring Engine",
    version="1.0.0",
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


@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "Dynamic Risk Trigger Engine",
        "version": "1.0.0",
        "docs_url": "/docs"
    }
