"""
Shared test fixtures.

Phase 4 put every business endpoint behind JWT auth, so tests that call the API
now need a token. Rather than stubbing the dependency out — which would leave the
RBAC layer untested and let a broken gate pass CI — these fixtures authenticate
for real against the demo accounts.
"""
import pytest
from fastapi.testclient import TestClient

from app.auth import seed_demo_users
from app.database import SessionLocal, init_db_schema
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def database():
    """Applies migrations and the audit triggers once for the whole session."""
    init_db_schema()
    db = SessionLocal()
    try:
        seed_demo_users(db)
    finally:
        db.close()
    yield


@pytest.fixture(scope="session")
def client(database):
    # Context-managed so the lifespan runs and startup wiring is exercised.
    with TestClient(app) as test_client:
        yield test_client


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def analyst_headers(client):
    """ANALYST: can view and dispose of alerts; cannot escalate or export."""
    return {"Authorization": f"Bearer {_token(client, 'a.chen', 'analyst123')}"}


@pytest.fixture(scope="session")
def manager_headers(client):
    """MANAGER: everything an analyst can do, plus escalate, export, evaluate."""
    return {"Authorization": f"Bearer {_token(client, 'r.okafor', 'manager123')}"}


@pytest.fixture(scope="session")
def auditor_headers(client):
    """AUDITOR: read-only, including audit export; cannot act on alerts."""
    return {"Authorization": f"Bearer {_token(client, 'j.lindqvist', 'auditor123')}"}
