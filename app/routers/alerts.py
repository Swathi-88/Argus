"""
Alert queue and analyst disposition.

Viewing is open to every role. Acting is gated: an Auditor can read the queue
but cannot dispose of anything, and escalation to MLRO needs a Manager.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, desc
from sqlalchemy.orm import Session, joinedload

from app import audit
from app.auth import (
    ACTION_PERMISSION,
    P_VIEW_ALERTS,
    Principal,
    require,
)
from app.database import get_db
from app.models import Alert, Customer, Event
from app.schemas import AlertActionRequest, AlertListResponse, AlertResponse

router = APIRouter(prefix="/alerts", tags=["Alert Management"])

# Queue priority: worst destination tier first. Alerts are worked top-down, so
# this ordering is the product decision that matters most in the whole view.
TIER_SEVERITY_ORDER = case(
    (Alert.new_tier == "CRITICAL", 1),
    (Alert.new_tier == "HIGH", 2),
    (Alert.new_tier == "MEDIUM", 3),
    (Alert.new_tier == "LOW", 4),
    else_=5,
)

VALID_ACTIONS = {"CONFIRMED", "DISMISSED", "ESCALATED", "INFO_REQUESTED"}

# Wording for the audit record, so the trail reads in the analyst's language.
ACTION_AUDIT_NAMES = {
    "CONFIRMED": "ANALYST_ACTION_CONFIRMED",
    "DISMISSED": "ANALYST_ACTION_DISMISSED",
    "ESCALATED": "ANALYST_ACTION_ESCALATED",
    "INFO_REQUESTED": "ANALYST_ACTION_INFO_REQUESTED",
}


def _build_response(
    alert: Alert,
    customer: Optional[Customer],
    trigger: Optional[Event],
    priority_rank: Optional[int] = None,
) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        customer_id=alert.customer_id,
        customer_name=customer.name if customer else None,
        customer_country=customer.country if customer else None,
        customer_industry=customer.industry if customer else None,
        trigger_event_id=alert.trigger_event_id,
        trigger_event_type=trigger.event_type if trigger else None,
        trigger_event_category=trigger.category if trigger else None,
        trigger_event_severity=trigger.severity if trigger else None,
        trigger_source=trigger.source if trigger else None,
        previous_tier=alert.previous_tier,
        new_tier=alert.new_tier,
        previous_score=alert.previous_score,
        new_score=alert.new_score,
        previous_log_odds=alert.previous_log_odds,
        new_log_odds=alert.new_log_odds,
        status=alert.status,
        recommended_action=alert.recommended_action,
        breakdown=alert.breakdown or {},
        priority_rank=priority_rank,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


@router.get("/queue", response_model=AlertListResponse, summary="Get prioritized alert queue")
def get_alert_queue(
    status_filter: Optional[str] = Query(
        "ALL",
        alias="status",
        description="NEW, CONFIRMED, DISMISSED, ESCALATED, INFO_REQUESTED, or ALL",
    ),
    tier_filter: Optional[str] = Query(None, alias="tier", description="LOW, MEDIUM, HIGH, CRITICAL"),
    search: Optional[str] = Query(None, description="Match on customer name"),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_ALERTS)),
):
    query = db.query(Alert)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(Alert.status == status_filter.upper())
    if tier_filter:
        query = query.filter(Alert.new_tier == tier_filter.upper())
    if search and search.strip():
        query = query.join(Customer, Alert.customer_id == Customer.id).filter(
            Customer.name.ilike(f"%{search.strip()}%")
        )

    total = query.count()

    # Tier severity first, then the size of the score jump, then recency — a
    # CRITICAL that moved 40 points outranks a CRITICAL that moved 2.
    rows: List[Alert] = (
        query.order_by(
            TIER_SEVERITY_ORDER,
            desc(Alert.new_score - Alert.previous_score),
            desc(Alert.created_at),
        )
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    # Batch the customer and trigger-event lookups: the previous per-row queries
    # made the queue O(2n) round trips.
    customer_ids = {r.customer_id for r in rows}
    event_ids = {r.trigger_event_id for r in rows if r.trigger_event_id}

    customers = (
        {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()}
        if customer_ids
        else {}
    )
    events = (
        {e.id: e for e in db.query(Event).filter(Event.id.in_(event_ids)).all()}
        if event_ids
        else {}
    )

    base_rank = (page - 1) * size + 1
    return AlertListResponse(
        total=total,
        page=page,
        size=size,
        alerts=[
            _build_response(
                r,
                customers.get(r.customer_id),
                events.get(r.trigger_event_id) if r.trigger_event_id else None,
                priority_rank=base_rank + i,
            )
            for i, r in enumerate(rows)
        ],
    )


@router.get("/{alert_id}", response_model=AlertResponse, summary="Get a single alert")
def get_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_ALERTS)),
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert with ID {alert_id} not found.")

    customer = db.query(Customer).filter(Customer.id == alert.customer_id).first()
    trigger = (
        db.query(Event).filter(Event.id == alert.trigger_event_id).first()
        if alert.trigger_event_id
        else None
    )
    return _build_response(alert, customer, trigger)


@router.post(
    "/{alert_id}/action",
    response_model=AlertResponse,
    summary="Record an analyst disposition on an alert",
)
def take_alert_action(
    alert_id: int,
    req: AlertActionRequest,
    db: Session = Depends(get_db),
    # Authenticated here; the specific permission depends on which action was
    # asked for, so it is checked below rather than in the dependency.
    principal: Principal = Depends(require(P_VIEW_ALERTS)),
):
    act = req.action.upper().strip()
    if act not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{req.action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}",
        )

    # ESCALATED needs alerts:escalate; the rest need alerts:act. An Auditor holds
    # neither, so their attempt is refused and the refusal is recorded.
    needed = ACTION_PERMISSION[act]
    if not principal.can(needed):
        audit.record(
            db,
            entity_type="ALERT",
            entity_id=alert_id,
            action="PERMISSION_DENIED",
            actor=principal.username,
            actor_role=principal.role,
            details={
                "attempted_action": act,
                "required_permission": needed,
                "role": principal.role,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Role {principal.role} cannot perform '{act}' — it requires the "
                f"'{needed}' permission."
            ),
        )

    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert with ID {alert_id} not found.")

    previous_status = alert.status
    alert.status = act

    if req.notes:
        # breakdown is a JSON column; rebind rather than mutate in place so
        # SQLAlchemy registers the change.
        breakdown = dict(alert.breakdown or {})
        breakdown["analyst_notes"] = req.notes
        breakdown["analyst_notes_by"] = principal.username
        alert.breakdown = breakdown

    db.commit()
    db.refresh(alert)

    audit.record(
        db,
        entity_type="ALERT",
        entity_id=alert.id,
        customer_id=alert.customer_id,
        action=ACTION_AUDIT_NAMES[act],
        actor=principal.username,
        actor_role=principal.role,
        details={
            "alert_id": alert.id,
            "disposition": act,
            "previous_status": previous_status,
            "notes": req.notes,
            "tier_transition": f"{alert.previous_tier} -> {alert.new_tier}",
            "risk_score_at_decision": alert.new_score,
            "trigger_event_id": alert.trigger_event_id,
            "decided_by": principal.full_name,
        },
    )

    customer = db.query(Customer).filter(Customer.id == alert.customer_id).first()
    trigger = (
        db.query(Event).filter(Event.id == alert.trigger_event_id).first()
        if alert.trigger_event_id
        else None
    )
    return _build_response(alert, customer, trigger)


@router.get("/stats/summary", summary="Queue counters for the dashboard header")
def alert_queue_stats(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_ALERTS)),
):
    """Single grouped query rather than one COUNT per tile."""
    from sqlalchemy import func

    by_status = dict(
        db.query(Alert.status, func.count(Alert.id)).group_by(Alert.status).all()
    )
    by_tier = dict(
        db.query(Alert.new_tier, func.count(Alert.id)).group_by(Alert.new_tier).all()
    )

    open_statuses = ("NEW", "INFO_REQUESTED")
    return {
        "total_alerts": sum(by_status.values()),
        "open_alerts": sum(by_status.get(s, 0) for s in open_statuses),
        "by_status": by_status,
        "by_tier": by_tier,
        "critical_open": db.query(Alert)
        .filter(Alert.new_tier == "CRITICAL", Alert.status.in_(open_statuses))
        .count(),
        "escalated": by_status.get("ESCALATED", 0),
    }
