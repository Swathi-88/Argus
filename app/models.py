from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.database import Base

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False)  # Corporate or Individual
    country = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    expected_turnover = Column(Float, nullable=False, default=0.0)
    is_pep = Column(Boolean, nullable=False, default=False)
    is_sanctioned = Column(Boolean, nullable=False, default=False)
    onboarding_date = Column(Date, nullable=False)
    risk_score = Column(Float, nullable=False, default=0.05)
    risk_tier = Column(String, nullable=False, default="LOW")  # LOW, MEDIUM, HIGH, CRITICAL
    log_odds = Column(Float, nullable=True)
    actual_turnover = Column(Float, nullable=False, default=0.0)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    aliases = relationship("EntityAlias", back_populates="customer", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="customer")
    materialized_events = relationship("MaterializedEvent", back_populates="customer")
    alerts = relationship("Alert", back_populates="customer", cascade="all, delete-orphan")

class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    alias_name = Column(String, nullable=False, index=True)
    alias_type = Column(String, nullable=False, default="TRADING_NAME")  # TRADING_NAME, FORMER_NAME, DBA, ACRONYM
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("Customer", back_populates="aliases")

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    entity_name = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)  # SANCTIONS_UPDATE, NEGATIVE_NEWS, TRANSACTION_SPIKE, etc.
    category = Column(String, nullable=False, default="CORPORATE_CHANGE") # REGULATORY_CHANGE, SANCTIONS_MATCH, ADVERSE_MEDIA, TRANSACTION_ANOMALY, CORPORATE_CHANGE
    severity = Column(String, nullable=False)    # LOW, MEDIUM, HIGH, CRITICAL
    source = Column(String, nullable=False)      # e.g., UK_Companies_House, OpenSanctions, FCA_Register, NewsAPI
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    matched_customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    match_confidence = Column(Float, nullable=False, default=0.0)
    match_method = Column(String, nullable=False, default="UNMATCHED")  # EXACT, NORMALIZED, FUZZY, UNMATCHED

    customer = relationship("Customer", back_populates="events")
    materialized_event = relationship("MaterializedEvent", back_populates="event", uselist=False)

class MaterializedEvent(Base):
    __tablename__ = "materialized_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    materiality_score = Column(Float, nullable=False)
    decision_reasons = Column(JSON, nullable=False)
    triggered_risk_recalculation = Column(Boolean, nullable=False, default=True)
    previous_risk_score = Column(Float, nullable=False)
    new_risk_score = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    event = relationship("Event", back_populates="materialized_event")
    customer = relationship("Customer", back_populates="materialized_events")

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_tier = Column(String, nullable=False)
    new_tier = Column(String, nullable=False)
    previous_score = Column(Float, nullable=False)
    new_score = Column(Float, nullable=False)
    previous_log_odds = Column(Float, nullable=False)
    new_log_odds = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="NEW", index=True)  # NEW, CONFIRMED, DISMISSED, ESCALATED, INFO_REQUESTED
    recommended_action = Column(String, nullable=False)
    breakdown = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", back_populates="alerts")
    trigger_event = relationship("Event")

class AuditLog(Base):
    """
    Append-only, hash-chained audit trail.

    UPDATE, DELETE and TRUNCATE are blocked by PostgreSQL triggers installed in
    app/db_constraints.py — the ORM cannot rewrite a row here even if asked.
    Each record additionally carries the hash of its predecessor, so removing or
    editing a record at the database file level breaks the chain detectably.
    Written exclusively through app/audit.py.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    # Monotonic position in the chain. Distinct from `id` so the chain stays
    # verifiable even if the sequence backing `id` is ever reset.
    sequence_no = Column(Integer, nullable=True, index=True)
    entity_type = Column(String, nullable=False)  # EVENT, CUSTOMER, RESOLUTION, MATERIALITY, ALERT, AUTH, SYSTEM
    entity_id = Column(Integer, nullable=False)
    # Denormalised so the per-customer audit trail is a single indexed read.
    customer_id = Column(Integer, nullable=True, index=True)
    action = Column(String, nullable=False)       # EVENT_INGESTED_AND_RESOLVED, ALERT_STATUS_UPDATED, etc.
    details = Column(JSON, nullable=True)
    # Who caused this record. "SYSTEM" for pipeline actions, a username for
    # analyst decisions — the distinction an auditor needs.
    actor = Column(String, nullable=True, index=True)
    actor_role = Column(String, nullable=True)
    prev_hash = Column(String, nullable=True)
    record_hash = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AnalystUser(Base):
    """
    Compliance staff accounts backing JWT authentication.
    Role is one of ANALYST, MANAGER, AUDITOR — see app/auth.py for the
    role-to-permission matrix.
    """
    __tablename__ = "analyst_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    role = Column(String, nullable=False, default="ANALYST")
    # PBKDF2-HMAC-SHA256, salt stored inline. Format: pbkdf2_sha256$<iters>$<salt>$<hash>
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class EvaluationRun(Base):
    """
    A completed baseline-vs-engine evaluation. Persisted so the comparison
    report is reproducible and citable rather than recomputed per page load.
    """
    __tablename__ = "evaluation_runs"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=False, default="baseline_vs_engine")
    random_seed = Column(Integer, nullable=False, default=42)
    num_customers = Column(Integer, nullable=False)
    num_events = Column(Integer, nullable=False)
    horizon_days = Column(Integer, nullable=False)
    config = Column(JSON, nullable=False)
    # Metric blocks: engine, baseline, entity_resolution, deltas.
    metrics = Column(JSON, nullable=False)
    # Pre-shaped series for the comparison charts.
    chart_data = Column(JSON, nullable=False)
    runtime_seconds = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


