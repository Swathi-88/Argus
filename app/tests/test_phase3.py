import pytest
import math
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.db_constraints import install_audit_immutability
from app.main import app
from app.models import Customer, Event, MaterializedEvent, Alert, AuditLog
from app.risk_engine import (
    PriorRiskModel, BayesianRiskEngine, risk_engine,
    log_odds_to_probability, map_probability_to_tier
)
from app.anomaly_detector import TransactionAnomalyDetector


# These tests run against a throwaway PostgreSQL *schema* rather than SQLite.
# SQLite is no longer an option: the audit chain serialises appends with
# pg_advisory_xact_lock and the append-only guarantee is a Postgres trigger, so a
# SQLite test double would exercise neither. A dedicated schema keeps the
# destructive drop_all away from the demo data while still testing the real
# database engine.
TEST_SCHEMA = "test_phase3"

engine = create_engine(
    settings.DATABASE_URL,
    # search_path is the test schema ONLY — deliberately without `public`.
    # With public on the path, create_all's has_table check finds the real
    # public.audit_logs, skips creating the test copy, and every write in these
    # tests lands in the live table instead. Built-ins live in pg_catalog and are
    # still reachable, so nothing else is lost.
    connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db():
    # The override is applied per-test and removed afterwards. Setting it at
    # import time would leak this schema into every other test module,
    # depending on collection order.
    app.dependency_overrides[get_db] = override_get_db

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))

    Base.metadata.create_all(bind=engine)
    install_audit_immutability(engine)

    yield

    app.dependency_overrides.pop(get_db, None)
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))


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

        # The demo accounts live in the app's own schema, so authenticate
        # against that before switching the request session to the test schema.
        from app.auth import ROLE_ANALYST, ROLE_AUDITOR, create_access_token
        from app.models import AnalystUser

        analyst = AnalystUser(
            id=1, username="a.chen", full_name="Amara Chen",
            role=ROLE_ANALYST, password_hash="unused", is_active=True,
        )
        auditor = AnalystUser(
            id=3, username="j.lindqvist", full_name="Jo Lindqvist",
            role=ROLE_AUDITOR, password_hash="unused", is_active=True,
        )
        analyst_headers = {
            "Authorization": f"Bearer {create_access_token(analyst)['access_token']}"
        }
        auditor_headers = {
            "Authorization": f"Bearer {create_access_token(auditor)['access_token']}"
        }

        # 1. GET /alerts/queue
        resp_q = client.get("/alerts/queue?status=NEW", headers=analyst_headers)
        assert resp_q.status_code == 200
        data_q = resp_q.json()
        assert data_q["total"] >= 1
        assert data_q["alerts"][0]["id"] == alert.id
        assert data_q["alerts"][0]["customer_name"] == "Gamma Holdings"

        # 2. GET /customers/{id}/risk-explanation
        resp_exp = client.get(f"/customers/{cust.id}/risk-explanation", headers=analyst_headers)
        assert resp_exp.status_code == 200
        data_exp = resp_exp.json()
        assert data_exp["customer_id"] == cust.id
        assert "onboarding_math_breakdown" in data_exp
        assert "step_by_step_math" in data_exp

        # 3. An Auditor may read the queue but must not be able to dispose of it.
        resp_denied = client.post(
            f"/alerts/{alert.id}/action",
            headers=auditor_headers,
            json={"action": "CONFIRMED"},
        )
        assert resp_denied.status_code == 403, "AUDITOR must not be able to act on alerts"

        # 4. POST /alerts/{id}/action as the analyst
        resp_act = client.post(
            f"/alerts/{alert.id}/action",
            headers=analyst_headers,
            json={"action": "CONFIRMED", "notes": "High risk pattern confirmed by compliance analyst."}
        )
        assert resp_act.status_code == 200
        data_act = resp_act.json()
        assert data_act["status"] == "CONFIRMED"
        assert data_act["breakdown"]["analyst_notes"] == "High risk pattern confirmed by compliance analyst."

        # 5. Escalation needs a Manager, which the analyst is not.
        resp_escalate = client.post(
            f"/alerts/{alert.id}/action",
            headers=analyst_headers,
            json={"action": "ESCALATED"},
        )
        assert resp_escalate.status_code == 403, "ANALYST must not be able to escalate"

        # 6. The disposition is in the audit trail, attributed and hash-chained.
        db.expire_all()
        audit_entry = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == "ALERT",
                AuditLog.entity_id == alert.id,
                AuditLog.action == "ANALYST_ACTION_CONFIRMED",
            )
            .first()
        )
        assert audit_entry is not None
        assert audit_entry.actor == "a.chen"
        assert audit_entry.actor_role == ROLE_ANALYST
        assert audit_entry.record_hash and len(audit_entry.record_hash) == 64

        # The refused attempts are recorded too — a denial is evidence.
        denials = (
            db.query(AuditLog).filter(AuditLog.action == "PERMISSION_DENIED").count()
        )
        assert denials >= 2, "both refused attempts should be audited"
    finally:
        db.close()


def test_audit_chain_is_append_only_and_tamper_evident():
    """
    The two independent guarantees, checked separately:
    the trigger refuses mutation, and the hash chain detects content changes.
    """
    from app import audit as audit_service
    from app.db_constraints import verify_audit_immutability

    db = TestingSessionLocal()
    try:
        first = audit_service.record(
            db, entity_type="SYSTEM", entity_id=0, action="SYSTEM_STARTUP",
            details={"n": 1},
        )
        second = audit_service.record(
            db, entity_type="SYSTEM", entity_id=0, action="SEED_DATABASE",
            details={"n": 2},
        )

        # Chained: each record points at its predecessor.
        assert first.sequence_no == 1
        assert first.prev_hash == audit_service.GENESIS_HASH
        assert second.sequence_no == 2
        assert second.prev_hash == first.record_hash

        result = audit_service.verify_chain(db)
        assert result["chain_valid"] is True
        assert result["records_verified"] == 2
        assert result["breaks"] == []
    finally:
        db.close()

    # UPDATE and DELETE are refused by the database, not by application code.
    proof = verify_audit_immutability(engine)
    assert proof["update_blocked"] is True
    assert proof["delete_blocked"] is True
    assert proof["enforced"] is True
    assert "trg_audit_logs_no_update" in proof["triggers_present"]
    assert "trg_audit_logs_no_delete" in proof["triggers_present"]
    assert "trg_audit_logs_no_truncate" in proof["triggers_present"]


def test_hash_chain_detects_altered_content():
    """
    If a record is changed beneath the trigger — a restored backup, a superuser
    who disabled it — verification must name the record that changed.
    """
    from sqlalchemy.orm import Session as SqlSession

    from app import audit as audit_service

    db = TestingSessionLocal()
    try:
        audit_service.record(
            db, entity_type="SYSTEM", entity_id=0, action="SYSTEM_STARTUP",
            details={"original": True},
        )
        target_id = audit_service.record(
            db, entity_type="SYSTEM", entity_id=0, action="SEED_DATABASE",
            details={"original": True},
        ).id
        audit_service.record(
            db, entity_type="SYSTEM", entity_id=0, action="SYSTEM_STARTUP",
            details={"original": True},
        )
        assert audit_service.verify_chain(db)["chain_valid"] is True
    finally:
        db.close()

    conn = engine.connect()
    trans = conn.begin()
    try:
        conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_no_update"))
        conn.execute(
            text("UPDATE audit_logs SET action = 'ANALYST_ACTION_DISMISSED' WHERE id = :i"),
            {"i": target_id},
        )

        scoped = SqlSession(bind=conn)
        tampered = audit_service.verify_chain(scoped)
        scoped.close()

        assert tampered["chain_valid"] is False
        assert any(
            b["record_id"] == target_id and "does not match its stored hash" in b["reason"]
            for b in tampered["breaks"]
        ), f"expected a hash mismatch on record {target_id}, got {tampered['breaks']}"
    finally:
        # Always rolled back — the tamper never lands.
        trans.rollback()
        conn.close()
