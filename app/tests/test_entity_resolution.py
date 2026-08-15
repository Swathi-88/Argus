import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Customer, EntityAlias
from app.entity_resolution import EntityResolver, normalize_name

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    
    # Insert fixture data
    c1 = Customer(
        id=1, name="Barclays Global Logistics", type="Corporate", country="GB", industry="Banking & Finance",
        expected_turnover=1000000.0, is_pep=False, is_sanctioned=False, onboarding_date=date(2022, 1, 1),
        risk_score=25.0, risk_tier="LOW"
    )
    c2 = Customer(
        id=2, name="Apex Cybernetics Corporation", type="Corporate", country="US", industry="Defense & Aerospace",
        expected_turnover=5000000.0, is_pep=False, is_sanctioned=True, onboarding_date=date(2021, 5, 12),
        risk_score=85.0, risk_tier="CRITICAL"
    )
    c3 = Customer(
        id=3, name="Alexander Hamilton", type="Individual", country="US", industry="Legal & Consulting",
        expected_turnover=250000.0, is_pep=True, is_sanctioned=False, onboarding_date=date(2023, 8, 20),
        risk_score=55.0, risk_tier="MEDIUM"
    )
    session.add_all([c1, c2, c3])
    session.commit()

    alias1 = EntityAlias(customer_id=1, alias_name="BGL International", alias_type="TRADING_NAME")
    session.add(alias1)
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


def test_normalize_name():
    assert normalize_name("Acme Global Logistics Ltd.") == "acme global logistics"
    assert normalize_name("APEX CYBERNETICS CORPORATION, LLC") == "apex cybernetics"
    assert normalize_name("  Barclays   Group   S.A. ") == "barclays"


def test_exact_match(db_session):
    resolver = EntityResolver(fuzzy_threshold=65.0)
    
    # Exact case-insensitive match on Customer Name
    res1 = resolver.resolve("Barclays Global Logistics", db_session)
    assert res1.matched_customer_id == 1
    assert res1.confidence_score == 100.0
    assert res1.match_method == "EXACT"

    # Exact match on Alias Name
    res2 = resolver.resolve("BGL International", db_session)
    assert res2.matched_customer_id == 1
    assert res2.confidence_score == 100.0
    assert res2.match_method == "EXACT"


def test_normalized_match(db_session):
    resolver = EntityResolver(fuzzy_threshold=65.0)

    # Adding legal suffix "Inc." and lowercase string
    res = resolver.resolve("barclays global logistics inc.", db_session)
    assert res.matched_customer_id == 1
    assert res.confidence_score == 95.0
    assert res.match_method == "NORMALIZED"

    # Suffix modification on Apex Cybernetics
    res2 = resolver.resolve("Apex Cybernetics Ltd", db_session)
    assert res2.matched_customer_id == 2
    assert res2.confidence_score == 95.0
    assert res2.match_method == "NORMALIZED"


def test_fuzzy_match(db_session):
    resolver = EntityResolver(fuzzy_threshold=65.0)

    # Typo: "Barklays Globl Logistiks"
    res = resolver.resolve("Barklays Globl Logistiks", db_session)
    assert res.matched_customer_id == 1
    assert res.confidence_score >= 65.0
    assert res.match_method == "FUZZY"

    # Minor spelling variation: "Apex Sybernetics Corp"
    res2 = resolver.resolve("Apex Sybernetics Corp", db_session)
    assert res2.matched_customer_id == 2
    assert res2.confidence_score >= 65.0
    assert res2.match_method == "FUZZY"


def test_unmatched(db_session):
    resolver = EntityResolver(fuzzy_threshold=65.0)

    # Completely unrelated entity name
    res = resolver.resolve("Zebra Quantum Robotics 999", db_session)
    assert res.matched_customer_id is None
    assert res.match_method == "UNMATCHED"
    assert res.confidence_score < 65.0
