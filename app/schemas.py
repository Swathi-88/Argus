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
    customer_country: Optional[str] = None
    customer_industry: Optional[str] = None
    trigger_event_id: Optional[int] = None
    # The "primary trigger" column in the queue: what set this alert off.
    trigger_event_type: Optional[str] = None
    trigger_event_category: Optional[str] = None
    trigger_event_severity: Optional[str] = None
    trigger_source: Optional[str] = None
    previous_tier: str
    new_tier: str
    previous_score: float
    new_score: float
    previous_log_odds: float
    new_log_odds: float
    status: str
    recommended_action: str
    breakdown: Dict[str, Any]
    # Rank used for queue ordering: tier severity, then score movement, then recency.
    priority_rank: Optional[int] = None
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


# ==========================================================================
# Phase 4 — Authentication & RBAC
# ==========================================================================

class LoginRequest(BaseModel):
    username: str = Field(..., description="Analyst username", examples=["a.chen"])
    password: str = Field(..., description="Account password")


class PrincipalResponse(BaseModel):
    username: str
    user_id: Optional[int] = None
    full_name: str
    role: str
    permissions: List[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_at: str
    expires_in: int
    user: PrincipalResponse


class RoleMatrixResponse(BaseModel):
    roles: Dict[str, List[str]]
    descriptions: Dict[str, str]
    demo_accounts: List[Dict[str, str]]


# ==========================================================================
# Phase 4 — Risk timeline & audit trail
# ==========================================================================

class RiskTimelinePoint(BaseModel):
    """One vertex on the customer's risk-score evolution line."""
    sequence: int
    timestamp: datetime
    log_odds: float
    risk_score: float
    risk_tier: str
    # None for the onboarding-prior point, which has no triggering event.
    event_id: Optional[int] = None
    event_type: Optional[str] = None
    event_category: Optional[str] = None
    event_severity: Optional[str] = None
    likelihood_ratio: Optional[float] = None
    log_odds_delta: Optional[float] = None
    label: str


class RiskTimelineResponse(BaseModel):
    customer_id: int
    customer_name: str
    current_risk_score: float
    current_risk_tier: str
    current_log_odds: float
    onboarding_date: Optional[date] = None
    points: List[RiskTimelinePoint]
    # Tier thresholds so the chart can draw its bands without hardcoding them.
    tier_thresholds: Dict[str, float]


class AuditRecordResponse(BaseModel):
    id: int
    sequence_no: Optional[int] = None
    entity_type: str
    entity_id: int
    customer_id: Optional[int] = None
    action: str
    action_label: str
    # SYSTEM for pipeline steps, ANALYST for a human decision.
    origin: str
    actor: Optional[str] = None
    actor_role: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    prev_hash: Optional[str] = None
    record_hash: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditTrailResponse(BaseModel):
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    total: int
    page: int
    size: int
    records: List[AuditRecordResponse]
    chain_verified: bool
    head_hash: str


class ChainVerificationResponse(BaseModel):
    chain_valid: bool
    records_verified: int
    records_in_scope: int
    unchained_legacy_records: int
    head_hash: str
    head_sequence_no: int
    breaks: List[Dict[str, Any]]
    verified_at: str
    algorithm: str


class ImmutabilityProofResponse(BaseModel):
    """Result of actually attempting a forbidden write, inside a rolled-back transaction."""
    enforced: bool
    update_blocked: bool
    delete_blocked: bool
    update_error: Optional[str] = None
    delete_error: Optional[str] = None
    triggers_present: List[str]
    # "live_probe" when a real statement was refused; "trigger_inspection" when
    # the table was empty and no row-level trigger could be provoked.
    method: str = "live_probe"
    note: Optional[str] = None


# ==========================================================================
# Phase 4 — Live pipeline demo
# ==========================================================================

class PipelineStage(BaseModel):
    stage: str
    status: str
    duration_ms: float
    detail: Dict[str, Any]


class PipelineTraceResponse(BaseModel):
    """One injected event, with every stage it passed through."""
    event: EventResponse
    stages: List[PipelineStage]
    total_duration_ms: float
    materialized: bool
    materialized_event_id: Optional[int] = None
    alert_generated: bool
    alert_id: Optional[int] = None
    alert: Optional[AlertResponse] = None
    audit_records_written: int
    audit_records: List[AuditRecordResponse]


# ==========================================================================
# Phase 4 — Evaluation harness
# ==========================================================================

class EvaluationRunRequest(BaseModel):
    num_customers: int = Field(1000, ge=50, le=5000)
    num_events: int = Field(5000, ge=100, le=25000)
    horizon_days: int = Field(365, ge=90, le=1095)
    baseline_review_interval_days: int = Field(90, ge=30, le=365)
    random_seed: int = Field(42, description="Fixes the scenario so runs are reproducible")
    fuzzy_match_threshold: Optional[float] = Field(
        None,
        ge=50.0,
        le=100.0,
        description=(
            "Entity-resolution fuzzy threshold. Omit to use the production default. "
            "The report's threshold sensitivity table recommends a value; set it here to verify."
        ),
    )


class EvaluationRunResponse(BaseModel):
    id: int
    label: str
    random_seed: int
    num_customers: int
    num_events: int
    horizon_days: int
    config: Dict[str, Any]
    metrics: Dict[str, Any]
    chart_data: Dict[str, Any]
    runtime_seconds: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


