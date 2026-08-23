import pytest
import math
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Customer, Event, MaterializedEvent, Alert, AuditLog
from app.risk_engine import (
    PriorRiskModel, BayesianRiskEngine, risk_engine,
    log_odds_to_probability, map_probability_to_tier
)
from app.anomaly_detector import TransactionAnomalyDetector


# Setup Test Database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_phase3.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_prior_risk_model():
    """Verifies logistic-regression scoring for customer onboarding attributes."""
    prior_model = PriorRiskModel()

    # Low risk customer
    cust_low = Customer(
        name="Low Risk Ltd",
        type="Corporate",
        country="GB",
        industry="Retail",
        expected_turnover=100_000,
        actual_turnover=100_000,
        is_pep=False,
        is_sanctioned=False
    )
    prob, log_odds, breakdown = prior_model.calculate_prior(cust_low)
    
    assert prob < 0.20
    assert map_probability_to_tier(prob) == "LOW"
    assert "equation" in breakdown

    # High risk PEP customer in high risk country & industry
    cust_high = Customer(
        name="High Risk Capital",
        type="Corporate",
        country="KY",
        industry="Crypto & Digital Assets",
        expected_turnover=500_000,
        actual_turnover=2_500_000,
        is_pep=True,
        is_sanctioned=False
    )
    prob_h, log_odds_h, _ = prior_model.calculate_prior(cust_high)
    assert prob_h > prob
    assert log_odds_h > log_odds


def test_bayesian_log_odds_updates_and_alerts():
    """Verifies Bayesian log-odds update math and tier boundary alert generation."""
    db = TestingSessionLocal()
    try:
        cust = Customer(
            name="Alpha Corp",
            type="Corporate",
            country="GB",
            industry="Manufacturing",
            expected_turnover=200_000,
            actual_turnover=200_000,
            is_pep=False,
            is_sanctioned=False,
            onboarding_date=date(2025, 1, 1)
        )
        prior_p, prior_lo, _ = risk_engine.calculate_prior(cust)
        cust.risk_score = prior_p
        cust.log_odds = prior_lo
        cust.risk_tier = map_probability_to_tier(prior_p)
        db.add(cust)
        db.commit()
        db.refresh(cust)

        start_tier = cust.risk_tier

        # Inject TXN_ANOMALY (LR = 6.0, Δ = ln(6.0) ≈ 1.7918)
        event = Event(
            entity_name=cust.name,
            event_type="TXN_ANOMALY",
            category="TRANSACTION_ANOMALY",
            severity="HIGH",
            source="Test_Engine",
            matched_customer_id=cust.id,
            match_confidence=100.0,
            match_method="EXACT"
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        new_p, new_lo, old_t, new_t, tier_crossed, math_exp = risk_engine.update_customer_risk(
            customer=cust,
            event=event,
            db=db
        )

        expected_lo = round(prior_lo + math.log(6.0), 4)
        expected_p = round(log_odds_to_probability(expected_lo), 4)

        assert new_lo == expected_lo
        assert new_p == expected_p
        assert math_exp["likelihood_ratio_LR"] == 6.0

        if tier_crossed:
            alert = db.query(Alert).filter(Alert.customer_id == cust.id).first()
            assert alert is not None
            assert alert.previous_tier == old_t
            assert alert.new_tier == new_t
            assert "log_odds_addition_delta" in alert.breakdown
    finally:
        db.close()


def test_isolation_forest_anomaly_detector():
    """Verifies IsolationForest training, synthetic transaction generation, and anomaly evaluation."""
    db = TestingSessionLocal()
    try:
        detector = TransactionAnomalyDetector()
        assert detector.is_trained

        cust = Customer(
            name="Beta Logistics",
            type="Corporate",
            country="GB",
            industry="Logistics",
            expected_turnover=1_000_000,
            actual_turnover=1_000_000,
            is_pep=False,
            is_sanctioned=False,
            onboarding_date=date(2025, 1, 1)
        )
        db.add(cust)
        db.commit()
        db.refresh(cust)

        # Batch of normal + suspicious transactions
        txns = [
            {"amount": 1200.0, "velocity_24h": 1, "deviation_from_turnover": 0.8, "counterparty_country": "GB"},
            {"amount": 150000.0, "velocity_24h": 20, "deviation_from_turnover": 15.0, "counterparty_country": "KY"}
        ]

        res = detector.detect_anomalies_in_batch(customer=cust, transactions=txns, db=db, auto_trigger_event=True)
        assert res["evaluated_count"] == 2
        assert res["anomalies_detected"] >= 1
        assert len(res["event_ids_created"]) >= 1
    finally:
        db.close()


def test_fastapi_alerts_and_explanation_endpoints():
    """Verifies GET /alerts/queue, GET /customers/{id}/risk-explanation, and POST /alerts/{id}/action APIs."""
    db = TestingSessionLocal()
    try:
        cust = Customer(
            name="Gamma Holdings",
            type="Corporate",
            country="DE",
            industry="Banking & Finance",
            expected_turnover=5_000_000,
            actual_turnover=5_000_000,
            is_pep=False,
            is_sanctioned=False,
            onboarding_date=date(2025, 1, 1),
            risk_score=0.15,
            log_odds=-1.7346,
            risk_tier="LOW"
        )
        db.add(cust)
        db.commit()
        db.refresh(cust)

        # Create alert manually
        alert = Alert(
            customer_id=cust.id,
            previous_tier="LOW",
            new_tier="HIGH",
            previous_score=0.15,
            new_score=0.65,
            previous_log_odds=-1.7346,
            new_log_odds=0.6190,
            status="NEW",
            recommended_action="Perform Enhanced Due Diligence (EDD)",
            breakdown={"test": "math breakdown"}
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)

        # 1. Test GET /alerts/queue
        resp_q = client.get("/alerts/queue?status=NEW")
        assert resp_q.status_code == 200
        data_q = resp_q.json()
        assert data_q["total"] >= 1
        assert data_q["alerts"][0]["id"] == alert.id
        assert data_q["alerts"][0]["customer_name"] == "Gamma Holdings"

        # 2. Test GET /customers/{id}/risk-explanation
        resp_exp = client.get(f"/customers/{cust.id}/risk-explanation")
        assert resp_exp.status_code == 200
        data_exp = resp_exp.json()
        assert data_exp["customer_id"] == cust.id
        assert "onboarding_math_breakdown" in data_exp
        assert "step_by_step_math" in data_exp

        # 3. Test POST /alerts/{id}/action
        resp_act = client.post(
            f"/alerts/{alert.id}/action",
            json={"action": "CONFIRMED", "notes": "High risk pattern confirmed by compliance analyst."}
        )
        assert resp_act.status_code == 200
        data_act = resp_act.json()
        assert data_act["status"] == "CONFIRMED"
        assert data_act["breakdown"]["analyst_notes"] == "High risk pattern confirmed by compliance analyst."

        # Verify Audit Log entry
        audit = db.query(AuditLog).filter(AuditLog.entity_type == "ALERT", AuditLog.entity_id == alert.id).first()
        assert audit is not None
        assert audit.action == "ALERT_ACTION_CONFIRMED"
    finally:
        db.close()
