import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.db_constraints import install_audit_immutability
from app.models import Customer, Event, MaterializedEvent, AuditLog
from app.classifier import SignalClassifier
from app.materiality import MaterialityGate
from app.connectors.companies_house import CompaniesHouseConnector
from app.connectors.opensanctions import OpenSanctionsConnector
from app.connectors.fca_register import FCARegisterConnector
from app.connectors.news_api import NewsAPIConnector
from app.connectors.manager import ConnectorManager
from app.queue import event_queue
from app.worker import WorkerProcess
from app.seed import calculate_initial_risk

# A throwaway PostgreSQL schema, not SQLite: the worker pipeline under test
# writes hash-chained audit records, and the chain serialises appends with
# pg_advisory_xact_lock. On SQLite that function does not exist, so an in-memory
# double would fail on the very behaviour these tests exist to cover.
TEST_SCHEMA = "test_phase2"


@pytest.fixture
def db_session():
    engine = create_engine(
        settings.DATABASE_URL,
        # Test schema only — with `public` also on the path, create_all would
        # find the live tables, skip creating the test copies, and these tests
        # would write into the demo database.
        connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))

    Base.metadata.create_all(bind=engine)
    install_audit_immutability(engine)

    db = TestingSessionLocal()
    
    # Create sample synthetic test customers
    cust1 = Customer(
        name="Barclays Capital WealthTek Ltd",
        type="Corporate",
        country="GB",
        industry="Fintech",
        expected_turnover=5_000_000.0,
        actual_turnover=5_000_000.0,
        is_pep=False,
        is_sanctioned=False,
        onboarding_date=datetime.now().date(),
        risk_score=0.10,
        log_odds=-2.1972,
        risk_tier="LOW"
    )
    cust2 = Customer(
        name="John Jonathan Smith",
        type="Individual",
        country="GB",
        industry="Legal & Consulting",
        expected_turnover=150_000.0,
        actual_turnover=150_000.0,
        is_pep=False,
        is_sanctioned=False,
        onboarding_date=datetime.now().date(),
        risk_score=0.05,
        log_odds=-2.9444,
        risk_tier="LOW"
    )
    db.add_all([cust1, cust2])
    db.commit()
    
    yield db

    db.close()
    # Dropping the schema is cleaner than drop_all here: the audit triggers and
    # their function live in the schema too, and CASCADE takes the lot.
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
    engine.dispose()


# --- 1. Signal Classifier Tests ---
def test_signal_classifier_event_type_mapping():
    classifier = SignalClassifier()
    cat, sev, weight = classifier.classify("SANCTIONS_UPDATE")
    assert cat == "SANCTIONS_MATCH"
    assert sev == "CRITICAL"
    assert weight == 50.0

    cat, sev, weight = classifier.classify("CLIENT_MONEY_REVOCATION")
    assert cat == "REGULATORY_CHANGE"
    assert sev == "CRITICAL"
    assert weight == 35.0

    cat, sev, weight = classifier.classify("POLICE_RAID")
    assert cat == "LAW_ENFORCEMENT"
    assert sev == "CRITICAL"
    assert weight == 30.0

    cat, sev, weight = classifier.classify("PEP_LISTING")
    assert cat == "PEP_STATUS"
    assert sev == "HIGH"
    assert weight == 12.0

    cat, sev, weight = classifier.classify("DIRECTOR_CHANGE")
    assert cat == "CORPORATE_CHANGE"
    assert sev == "MEDIUM"
    assert weight == 3.0



def test_signal_classifier_keyword_search():
    classifier = SignalClassifier()
    cat, sev, weight = classifier.classify(
        event_type="UNSPECIFIED_SIGNAL",
        raw_payload={"details": "Investigation into bribery and fraud allegations"}
    )
    assert cat == "ADVERSE_MEDIA"
    assert sev in ("MEDIUM", "HIGH")


# --- 2. Materiality Gate Tests ---
def test_materiality_gate_domain_relevance(db_session):
    gate = MaterialityGate(confidence_threshold=65.0, dedup_window_hours=24)
    
    corp_cust = db_session.query(Customer).filter(Customer.type == "Corporate").first()
    indiv_cust = db_session.query(Customer).filter(Customer.type == "Individual").first()

    # Corporate change relevant for corporate customer
    rel, msg = gate.is_category_relevant(corp_cust, "CORPORATE_CHANGE", "DIRECTOR_CHANGE", {})
    assert rel is True

    # Corporate change NOT relevant for individual customer
    rel, msg = gate.is_category_relevant(indiv_cust, "CORPORATE_CHANGE", "DIRECTOR_CHANGE", {})
    assert rel is False

    # Sanctions relevant for both
    rel, msg = gate.is_category_relevant(indiv_cust, "SANCTIONS_MATCH", "SANCTIONS_UPDATE", {})
    assert rel is True


def test_materiality_gate_deduplication(db_session):
    gate = MaterialityGate(confidence_threshold=65.0, dedup_window_hours=24)
    cust = db_session.query(Customer).first()

    # Add existing event in DB
    ev1 = Event(
        entity_name=cust.name,
        event_type="SANCTIONS_UPDATE",
        category="SANCTIONS_MATCH",
        severity="CRITICAL",
        source="OpenSanctions",
        matched_customer_id=cust.id,
        match_confidence=100.0,
        match_method="EXACT"
    )
    db_session.add(ev1)
    db_session.commit()

    # Check deduplication for second identical event category
    is_dup, msg = gate.is_duplicate(cust.id, "SANCTIONS_MATCH", "SANCTIONS_UPDATE", db_session)
    assert is_dup is True
    assert "Duplicate event detected" in msg


# --- 3. External Connectors Tests ---
def test_companies_house_connector():
    conn = CompaniesHouseConnector()
    events = conn.fetch_events_for_customer("Barclays Capital WealthTek Ltd", "Corporate")
    assert isinstance(events, list)


def test_opensanctions_connector():
    conn = OpenSanctionsConnector()
    events = conn.fetch_events_for_customer("Barclays Capital WealthTek Ltd", "Corporate")
    assert isinstance(events, list)


def test_fca_register_connector_wealthtek():
    conn = FCARegisterConnector()
    events = conn.fetch_events_for_customer("Barclays Capital WealthTek Ltd", "Corporate")
    assert isinstance(events, list)


def test_news_api_connector():
    conn = NewsAPIConnector()
    events = conn.fetch_events_for_customer("Barclays Capital WealthTek Ltd", "Corporate")
    assert isinstance(events, list)


# --- 4. Queue & Worker Pipeline End-to-End Test ---
def test_worker_pipeline_end_to_end(db_session):
    worker = WorkerProcess()
    cust = db_session.query(Customer).filter(Customer.name.like("%WealthTek%")).first()
    initial_risk = cust.risk_score

    payload = {
        "entity_name": cust.name,
        "event_type": "CLIENT_MONEY_REVOCATION",
        "category": "REGULATORY_CHANGE",
        "severity": "CRITICAL",
        "source": "FCA_Register",
        "raw_payload": {"wealthtek_check": "FAILED - Not authorized to hold client money"}
    }

    mat_event = worker.process_event_payload(payload, db_session)
    
    assert mat_event is not None
    assert mat_event.customer_id == cust.id
    assert mat_event.category == "REGULATORY_CHANGE"
    assert mat_event.severity == "CRITICAL"
    assert mat_event.triggered_risk_recalculation is True
    assert mat_event.new_risk_score > initial_risk


    # Verify Customer risk score was updated in DB
    updated_cust = db_session.query(Customer).filter(Customer.id == cust.id).first()
    assert updated_cust.risk_score == mat_event.new_risk_score

    # Verify MaterializedEvent persisted in DB
    saved_mat = db_session.query(MaterializedEvent).filter(MaterializedEvent.id == mat_event.id).first()
    assert saved_mat is not None
    assert saved_mat.event_id is not None
