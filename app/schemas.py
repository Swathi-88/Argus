from typing import Optional, List, Any, Dict
from datetime import date, datetime
from pydantic import BaseModel, Field, ConfigDict

# Alias Schemas
class EntityAliasResponse(BaseModel):
    id: int
    alias_name: str
    alias_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Customer Schemas
class CustomerBase(BaseModel):
    name: str
    type: str
    country: str
    industry: str
    expected_turnover: float
    is_pep: bool
    is_sanctioned: bool
    onboarding_date: date
    risk_score: float
    risk_tier: str

class CustomerResponse(CustomerBase):
    id: int
    last_updated: Optional[datetime] = None
    aliases: List[EntityAliasResponse] = []

    model_config = ConfigDict(from_attributes=True)

class CustomerListResponse(BaseModel):
    total: int
    page: int
    size: int
    customers: List[CustomerResponse]

# Event Schemas
class EventCreate(BaseModel):
    entity_name: str = Field("WealthTek Ltd", description="The incoming target string to match against customer entity database")
    event_type: str = Field("CLIENT_MONEY_REVOCATION", description="Incoming event type", json_schema_extra={"example": "SANCTIONS_UPDATE"})
    category: Optional[str] = Field(None, description="Optional category (REGULATORY_CHANGE, SANCTIONS_MATCH, ADVERSE_MEDIA, TRANSACTION_ANOMALY, CORPORATE_CHANGE)")
    severity: Optional[str] = Field(None, description="Optional severity (LOW, MEDIUM, HIGH, CRITICAL)")
    source: str = Field("FCA_Register", description="Event source", json_schema_extra={"example": "OpenSanctions_API"})
    raw_payload: Optional[Dict[str, Any]] = None

class EventResponse(BaseModel):
    id: int
    entity_name: str
    event_type: str
    category: str
    severity: str
    source: str
    raw_payload: Optional[Dict[str, Any]] = None
    created_at: datetime
    matched_customer_id: Optional[int] = None
    match_confidence: float
    match_method: str
    matched_customer_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class EventListResponse(BaseModel):
    total: int
    page: int
    size: int
    events: List[EventResponse]

# Materialized Event Schemas
class MaterializedEventResponse(BaseModel):
    id: int
    event_id: int
    customer_id: int
    category: str
    severity: str
    materiality_score: float
    decision_reasons: List[str]
    triggered_risk_recalculation: bool
    previous_risk_score: float
    new_risk_score: float
    created_at: datetime
    matched_customer_name: Optional[str] = None
    entity_name: Optional[str] = None
    event_type: Optional[str] = None
    source: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class MaterializedEventListResponse(BaseModel):
    total: int
    page: int
    size: int
    materialized_events: List[MaterializedEventResponse]

# Entity Resolution Result
class EntityResolutionResult(BaseModel):
    matched_customer_id: Optional[int]
    matched_customer_name: Optional[str]
    matched_string: Optional[str]
    confidence_score: float
    match_method: str  # EXACT, NORMALIZED, FUZZY, UNMATCHED

# Connector Schemas
class ConnectorRunRequest(BaseModel):
    connector: str = Field("all", description="Connector name: companies_house | opensanctions | fca_register | news_api | all")
    limit_customers: int = Field(10, ge=1, le=100, description="Max synthetic customers to process")


class ConnectorRunResult(BaseModel):
    connector: str
    events_pulled: int
    events_queued: int
    events_materialized: int
    details: Dict[str, Any]

# Alert Schemas
class AlertResponse(BaseModel):
    id: int
    customer_id: int
    customer_name: Optional[str] = None
    trigger_event_id: Optional[int] = None
    previous_tier: str
    new_tier: str
    previous_score: float
    new_score: float
    previous_log_odds: float
    new_log_odds: float
    status: str
    recommended_action: str
    breakdown: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class AlertListResponse(BaseModel):
    total: int
    page: int
    size: int
    alerts: List[AlertResponse]

class AlertActionRequest(BaseModel):
    action: str = Field(..., description="Analyst action: CONFIRMED | DISMISSED | ESCALATED | INFO_REQUESTED")
    notes: Optional[str] = Field(None, description="Optional notes or justification by analyst")

# Risk Explanation Schemas
class RiskExplanationResponse(BaseModel):
    customer_id: int
    customer_name: str
    customer_type: str
    country: str
    industry: str
    is_pep: bool
    is_sanctioned: bool
    current_risk_score: float
    current_risk_tier: str
    current_log_odds: float
    prior_score: float
    prior_log_odds: float
    onboarding_math_breakdown: Dict[str, Any]
    event_history: List[Dict[str, Any]]
    step_by_step_math: List[str]
    recommendation: str

# Transaction Anomaly Schemas
class SingleTransaction(BaseModel):
    amount: float
    currency: str = "GBP"
    counterparty_country: str = "GB"
    velocity_24h: int = 1
    deviation_from_turnover: float = 1.0

class TransactionAnomalyRequest(BaseModel):
    customer_id: int
    transactions: List[SingleTransaction]

class TransactionAnomalyResponse(BaseModel):
    customer_id: int
    total_transactions_analyzed: int
    anomalies_detected: int
    anomaly_events_generated: int
    details: List[Dict[str, Any]]


