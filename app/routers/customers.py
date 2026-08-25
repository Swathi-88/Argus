import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.auth import P_VIEW_CUSTOMERS, Principal, require
from app.database import get_db
from app.models import Customer
from app.schemas import (
    CustomerResponse,
    CustomerListResponse,
    RiskExplanationResponse,
    RiskTimelinePoint,
    RiskTimelineResponse,
)


router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("", response_model=CustomerListResponse)
def list_customers(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier (LOW, MEDIUM, HIGH, CRITICAL)"),
    is_pep: Optional[bool] = Query(None, description="Filter by PEP status"),
    is_sanctioned: Optional[bool] = Query(None, description="Filter by Sanctions status"),
    search: Optional[str] = Query(None, description="Search term for name, country, or industry"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    Retrieves a paginated list of customers with optional filters.
    """
    query = db.query(Customer).options(joinedload(Customer.aliases))

    if risk_tier:
        query = query.filter(Customer.risk_tier == risk_tier.upper())
    if is_pep is not None:
        query = query.filter(Customer.is_pep == is_pep)
    if is_sanctioned is not None:
        query = query.filter(Customer.is_sanctioned == is_sanctioned)
    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            (Customer.name.ilike(search_term)) |
            (Customer.country.ilike(search_term)) |
            (Customer.industry.ilike(search_term))
        )

    total = query.count()
    customers = query.order_by(Customer.id.asc()).offset((page - 1) * size).limit(size).all()

    return CustomerListResponse(
        total=total,
        page=page,
        size=size,
        customers=[CustomerResponse.model_validate(c) for c in customers]
    )


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    Retrieves a single customer profile by ID, including entity aliases.
    """
    customer = db.query(Customer).options(joinedload(Customer.aliases)).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {customer_id} not found."
        )
    return CustomerResponse.model_validate(customer)


@router.get("/{customer_id}/risk-explanation", response_model=RiskExplanationResponse)
def get_customer_risk_explanation(
    customer_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    Retrieves full human-readable and structured mathematical explanation of how a customer's risk score was reached.
    Includes onboarding prior calculation, event-by-event log-odds contributions, cumulative probabilities, and recommended action.
    """
    from app.models import MaterializedEvent, Event
    from app.risk_engine import risk_engine, get_recommended_action, log_odds_to_probability

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {customer_id} not found."
        )

    # 1. Calculate onboarding prior
    prior_p, prior_lo, prior_math = risk_engine.calculate_prior(customer)

    # 2. Query all materialized events for customer in chronological order
    mat_events = db.query(MaterializedEvent).filter(
        MaterializedEvent.customer_id == customer_id
    ).order_by(MaterializedEvent.created_at.asc()).all()

    running_lo = prior_lo
    running_p = prior_p
    event_history = []
    step_by_step_math = []

    step_by_step_math.append(f"[Step 0 - Onboarding Prior] Onboarding attributes logit = {prior_lo:.4f} ==> Prior P(high_risk) = {prior_p:.4f} ({prior_p*100:.2f}%)")

    for idx, me in enumerate(mat_events, start=1):
        ev = db.query(Event).filter(Event.id == me.event_id).first()
        ev_type = ev.event_type if ev else me.category
        ev_source = ev.source if ev else "System"

        lr, lr_rule = risk_engine.get_likelihood_ratio(me.category, me.severity, ev_type)
        delta_lo = round(math.log(lr), 4)

        prev_lo = running_lo
        prev_p = running_p
        running_lo = round(running_lo + delta_lo, 4)
        running_p = round(log_odds_to_probability(running_lo), 4)

        event_history.append({
            "step": idx,
            "event_id": me.event_id,
            "event_category": me.category,
            "event_severity": me.severity,
            "event_type": ev_type,
            "source": ev_source,
            "likelihood_ratio_LR": lr,
            "log_odds_delta": delta_lo,
            "previous_log_odds": prev_lo,
            "previous_score": prev_p,
            "new_log_odds": running_lo,
            "new_score": running_p
        })

        step_by_step_math.append(
            f"[Step {idx} - Event #{me.event_id} {me.category}:{me.severity}] "
            f"LR = {lr:.1f} (Δ log-odds = +{delta_lo:.4f}). "
            f"log_odds: {prev_lo:.4f} + {delta_lo:.4f} = {running_lo:.4f} ==> P(high_risk) = {running_p:.4f} ({running_p*100:.2f}%)"
        )

    current_lo = customer.log_odds if customer.log_odds is not None else running_lo
    current_p = customer.risk_score or running_p
    rec_action = get_recommended_action(customer.risk_tier)

    return RiskExplanationResponse(
        customer_id=customer.id,
        customer_name=customer.name,
        customer_type=customer.type,
        country=customer.country,
        industry=customer.industry,
        is_pep=customer.is_pep,
        is_sanctioned=customer.is_sanctioned,
        current_risk_score=current_p,
        current_risk_tier=customer.risk_tier,
        current_log_odds=current_lo,
        prior_score=prior_p,
        prior_log_odds=prior_lo,
        onboarding_math_breakdown=prior_math,
        event_history=event_history,
        step_by_step_math=step_by_step_math,
        recommendation=rec_action
    )


# Tier boundaries, published so the chart draws its bands from the engine's
# thresholds rather than a duplicated copy in the frontend.
TIER_THRESHOLDS = {"MEDIUM": 0.20, "HIGH": 0.50, "CRITICAL": 0.80}


@router.get(
    "/{customer_id}/risk-timeline",
    response_model=RiskTimelineResponse,
    summary="Risk score evolution over time",
)
def get_customer_risk_timeline(
    customer_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    The series behind the investigation page's line chart.

    Point 0 is the onboarding prior, timestamped at the onboarding date. Each
    subsequent point is one materialized event, carrying the likelihood ratio and
    the log-odds step that moved the score — so the chart and the "why" panel are
    reading the same numbers.
    """
    from datetime import datetime, time, timezone

    from app.models import Event, MaterializedEvent
    from app.risk_engine import log_odds_to_probability, map_probability_to_tier, risk_engine

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {customer_id} not found.",
        )

    prior_p, prior_lo, _ = risk_engine.calculate_prior(customer)

    onboarding_ts = (
        datetime.combine(customer.onboarding_date, time.min, tzinfo=timezone.utc)
        if customer.onboarding_date
        else datetime.now(timezone.utc)
    )

    points = [
        RiskTimelinePoint(
            sequence=0,
            timestamp=onboarding_ts,
            log_odds=prior_lo,
            risk_score=prior_p,
            risk_tier=map_probability_to_tier(prior_p),
            label="Onboarding prior",
        )
    ]

    # One join instead of a per-event lookup.
    rows = (
        db.query(MaterializedEvent, Event)
        .join(Event, MaterializedEvent.event_id == Event.id)
        .filter(MaterializedEvent.customer_id == customer_id)
        .order_by(MaterializedEvent.created_at.asc())
        .all()
    )

    running_lo = prior_lo
    for idx, (mat_event, event) in enumerate(rows, start=1):
        lr, _ = risk_engine.get_likelihood_ratio(
            mat_event.category, mat_event.severity, event.event_type
        )
        delta = round(math.log(lr), 4)
        running_lo = round(running_lo + delta, 4)
        running_p = round(log_odds_to_probability(running_lo), 4)

        points.append(
            RiskTimelinePoint(
                sequence=idx,
                timestamp=mat_event.created_at,
                log_odds=running_lo,
                risk_score=running_p,
                risk_tier=map_probability_to_tier(running_p),
                event_id=event.id,
                event_type=event.event_type,
                event_category=mat_event.category,
                event_severity=mat_event.severity,
                likelihood_ratio=lr,
                log_odds_delta=delta,
                label=f"{event.event_type} ({mat_event.severity})",
            )
        )

    return RiskTimelineResponse(
        customer_id=customer.id,
        customer_name=customer.name,
        current_risk_score=customer.risk_score or prior_p,
        current_risk_tier=customer.risk_tier or map_probability_to_tier(prior_p),
        current_log_odds=customer.log_odds if customer.log_odds is not None else prior_lo,
        onboarding_date=customer.onboarding_date,
        points=points,
        tier_thresholds=TIER_THRESHOLDS,
    )

