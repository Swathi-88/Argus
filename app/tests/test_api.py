import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.database as app_db
from app.database import Base, get_db
from app.models import Customer, EntityAlias
from app.main import app

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Override engine in app.database
app_db.engine = engine


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    c1 = Customer(
        id=101, name="Vanguard Financial Holdings", type="Corporate", country="US", industry="Banking & Finance",
        expected_turnover=10000000.0, is_pep=False, is_sanctioned=False, onboarding_date=date(2023, 1, 15),
        risk_score=30.0, risk_tier="LOW"
    )
    c2 = Customer(
        id=102, name="Sovereign Energy Partners", type="Corporate", country="AE", industry="Energy & Commodities",
        expected_turnover=45000000.0, is_pep=True, is_sanctioned=False, onboarding_date=date(2022, 6, 10),
        risk_score=68.0, risk_tier="HIGH"
    )
    session.add_all([c1, c2])
    session.commit()

    alias = EntityAlias(customer_id=101, alias_name="Vanguard Finance", alias_type="TRADING_NAME")
    session.add(alias)
    session.commit()
    session.close()

    yield

    Base.metadata.drop_all(bind=engine)


def test_health_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_post_events_fuzzy_match():
    payload = {
        "entity_name": "Vanguard Financl Holdings Ltd",
        "event_type": "SANCTIONS_MATCH",
        "severity": "HIGH",
        "source": "DowJones_Sanctions",
        "raw_payload": {"source_id": "DJ-88392", "flagged_country": "US"}
    }
    response = client.post("/events", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["matched_customer_id"] == 101
    assert data["matched_customer_name"] == "Vanguard Financial Holdings"
    assert data["match_method"] in ["NORMALIZED", "FUZZY"]
    assert data["match_confidence"] >= 65.0


def test_get_customers_list():
    response = client.get("/customers?size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2
    assert len(data["customers"]) >= 2


def test_get_customer_detail():
    response = client.get("/customers/101")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 101
    assert data["name"] == "Vanguard Financial Holdings"
    assert len(data["aliases"]) == 1
    assert data["aliases"][0]["alias_name"] == "Vanguard Finance"


def test_get_customer_not_found():
    response = client.get("/customers/999999")
    assert response.status_code == 404


def test_get_events_list():
    response = client.get("/events")
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert data["total"] >= 1
