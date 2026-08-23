import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import engine, Base, SessionLocal, init_db_schema
from app.models import Customer, EntityAlias
from app.seed import seed_database
from app.main import app

client = TestClient(app)

@pytest.fixture(autouse=True, scope="module")
def setup_test_db():
    init_db_schema()
    db: Session = SessionLocal()
    try:
        cust_count = db.query(Customer).count()
        if cust_count == 0:
            seed_database(db, target_count=10)
    finally:
        db.close()
    yield


def test_health_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_post_events_fuzzy_match():
    db = SessionLocal()
    try:
        cust = db.query(Customer).first()
        cust_name = cust.name
        cust_id = cust.id
    finally:
        db.close()

    fuzzy_name = cust_name + " Ltd"
    response = client.post("/events", json={
        "entity_name": fuzzy_name,
        "event_type": "SANCTIONS_MATCH",
        "severity": "HIGH",
        "source": "DowJones_Sanctions",
        "raw_payload": {"source_id": "DJ-88392", "flagged_country": "US"}
    })
    assert response.status_code == 201
    data = response.json()
    assert data["matched_customer_id"] == cust_id
    assert data["match_confidence"] >= 60.0


def test_get_customers_list():
    response = client.get("/customers?size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["customers"]) >= 1


def test_get_customer_detail():
    db = SessionLocal()
    try:
        cust = db.query(Customer).first()
        cust_id = cust.id
        cust_name = cust.name
    finally:
        db.close()

    response = client.get(f"/customers/{cust_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == cust_id
    assert data["name"] == cust_name


def test_get_customer_not_found():
    response = client.get("/customers/9999999")
    assert response.status_code == 404


def test_get_events_list():
    response = client.get("/events")
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
