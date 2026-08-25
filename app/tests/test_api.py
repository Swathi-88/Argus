"""
API surface tests.

`client` and the *_headers fixtures come from conftest.py and authenticate
against the real demo accounts, so these tests exercise the RBAC layer rather
than bypassing it.
"""
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Customer
from app.seed import seed_database


def _first_customer():
    db: Session = SessionLocal()
    try:
        if db.query(Customer).count() == 0:
            seed_database(db, target_count=10)
        customer = db.query(Customer).first()
        return customer.id, customer.name
    finally:
        db.close()


def test_health_endpoint(client):
    """Health is deliberately unauthenticated — a probe has no credentials."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_business_endpoints_require_authentication(client):
    for path in ("/customers", "/events", "/alerts/queue", "/audit"):
        assert client.get(path).status_code == 401, f"{path} should require a token"


def test_post_events_fuzzy_match(client, analyst_headers):
    cust_id, cust_name = _first_customer()

    response = client.post(
        "/events",
        headers=analyst_headers,
        json={
            "entity_name": f"{cust_name} Ltd",
            "event_type": "SANCTIONS_UPDATE",
            "severity": "HIGH",
            "source": "DowJones_Sanctions",
            "raw_payload": {"source_id": "DJ-88392", "flagged_country": "US"},
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["matched_customer_id"] == cust_id
    assert data["match_confidence"] >= 60.0


def test_get_customers_list(client, analyst_headers):
    response = client.get("/customers?size=10", headers=analyst_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["customers"]) >= 1


def test_get_customer_detail(client, analyst_headers):
    cust_id, cust_name = _first_customer()
    response = client.get(f"/customers/{cust_id}", headers=analyst_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == cust_id
    assert data["name"] == cust_name


def test_get_customer_not_found(client, analyst_headers):
    response = client.get("/customers/9999999", headers=analyst_headers)
    assert response.status_code == 404


def test_get_events_list(client, analyst_headers):
    response = client.get("/events", headers=analyst_headers)
    assert response.status_code == 200
    assert "events" in response.json()


def test_risk_timeline_starts_at_the_onboarding_prior(client, analyst_headers):
    cust_id, _ = _first_customer()
    response = client.get(f"/customers/{cust_id}/risk-timeline", headers=analyst_headers)
    assert response.status_code == 200

    data = response.json()
    assert data["points"], "the timeline always has at least the prior"

    first = data["points"][0]
    assert first["sequence"] == 0
    assert first["label"] == "Onboarding prior"
    # The prior has no triggering event by definition.
    assert first["event_id"] is None
    # Tier thresholds are published so the chart does not hardcode them.
    assert data["tier_thresholds"] == {"MEDIUM": 0.20, "HIGH": 0.50, "CRITICAL": 0.80}
