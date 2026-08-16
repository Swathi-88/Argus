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
    entity_name: str = Field(..., description="The incoming target string to match against customer entity database")
    event_type: str = Field(..., json_schema_extra={"example": "SANCTIONS_UPDATE"})
    category: Optional[str] = Field(None, description="Optional category (REGULATORY_CHANGE, SANCTIONS_MATCH, ADVERSE_MEDIA, TRANSACTION_ANOMALY, CORPORATE_CHANGE)")
    severity: Optional[str] = Field(None, description="Optional severity (LOW, MEDIUM, HIGH, CRITICAL)")
    source: str = Field(..., json_schema_extra={"example": "OpenSanctions_API"})
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
    connector: str = Field(..., description="Connector name: companies_house | opensanctions | fca_register | news_api | all")
    limit_customers: int = Field(10, ge=1, le=100, description="Max synthetic customers to process")

class ConnectorRunResult(BaseModel):
    connector: str
    events_pulled: int
    events_queued: int
    events_materialized: int
    details: Dict[str, Any]

