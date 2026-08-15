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
    risk_score = Column(Float, nullable=False, default=0.0)
    risk_tier = Column(String, nullable=False, default="LOW")  # LOW, MEDIUM, HIGH, CRITICAL
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    aliases = relationship("EntityAlias", back_populates="customer", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="customer")

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
    severity = Column(String, nullable=False)    # LOW, MEDIUM, HIGH, CRITICAL
    source = Column(String, nullable=False)      # e.g., DowJones, Refinitiv, Internal
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    matched_customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    match_confidence = Column(Float, nullable=False, default=0.0)
    match_method = Column(String, nullable=False, default="UNMATCHED")  # EXACT, NORMALIZED, FUZZY, UNMATCHED

    customer = relationship("Customer", back_populates="events")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)  # EVENT, CUSTOMER, RESOLUTION
    entity_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)       # EVENT_INGESTED_AND_RESOLVED, etc.
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
