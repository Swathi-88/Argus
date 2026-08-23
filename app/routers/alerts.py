from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import case, desc

from app.database import get_db
from app.models import Alert, Customer, AuditLog
from app.schemas import AlertResponse, AlertListResponse, AlertActionRequest

router = APIRouter(prefix="/alerts", tags=["Alert Management"])

TIER_SEVERITY_ORDER = case(
    (Alert.new_tier == "CRITICAL", 1),
    (Alert.new_tier == "HIGH", 2),
    (Alert.new_tier == "MEDIUM", 3),
    (Alert.new_tier == "LOW", 4),
    else_=5
)


@router.get("/queue", response_model=AlertListResponse, summary="Get prioritized alert queue")
def get_alert_queue(
    status_filter: Optional[str] = Query("ALL", alias="status", description="Filter by status: NEW, CONFIRMED, DISMISSED, ESCALATED, INFO_REQUESTED, or ALL"),
    tier_filter: Optional[str] = Query(None, alias="tier", description="Filter by new_tier: LOW, MEDIUM, HIGH, CRITICAL"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):

    query = db.query(Alert)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(Alert.status == status_filter.upper())
    
    if tier_filter:
        query = query.filter(Alert.new_tier == tier_filter.upper())

    total = query.count()

    # Prioritized by severity rank (CRITICAL > HIGH > MEDIUM > LOW) then recency
    alerts_raw = query.order_by(TIER_SEVERITY_ORDER, desc(Alert.created_at))\
                      .offset((page - 1) * size)\
                      .limit(size)\
                      .all()

    alert_responses = []
    for a in alerts_raw:
        cust = db.query(Customer).filter(Customer.id == a.customer_id).first()
        cust_name = cust.name if cust else None
        
        resp = AlertResponse(
            id=a.id,
            customer_id=a.customer_id,
            customer_name=cust_name,
            trigger_event_id=a.trigger_event_id,
            previous_tier=a.previous_tier,
            new_tier=a.new_tier,
            previous_score=a.previous_score,
            new_score=a.new_score,
            previous_log_odds=a.previous_log_odds,
            new_log_odds=a.new_log_odds,
            status=a.status,
            recommended_action=a.recommended_action,
            breakdown=a.breakdown,
            created_at=a.created_at,
            updated_at=a.updated_at
        )
        alert_responses.append(resp)

    return AlertListResponse(
        total=total,
        page=page,
        size=size,
        alerts=alert_responses
    )


@router.post("/{alert_id}/action", response_model=AlertResponse, summary="Perform analyst action on alert")
def take_alert_action(
    alert_id: int,
    req: AlertActionRequest,
    db: Session = Depends(get_db)
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert with ID {alert_id} not found.")

    valid_actions = {"CONFIRMED", "DISMISSED", "ESCALATED", "INFO_REQUESTED"}
    act_upper = req.action.upper()
    if act_upper not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{req.action}'. Must be one of: {', '.join(valid_actions)}"
        )

    prev_status = alert.status
    alert.status = act_upper
    
    # Store notes inside breakdown if provided
    if req.notes:
        breakdown_dict = dict(alert.breakdown or {})
        breakdown_dict["analyst_notes"] = req.notes
        alert.breakdown = breakdown_dict

    db.commit()
    db.refresh(alert)

    # Log audit entry
    audit = AuditLog(
        entity_type="ALERT",
        entity_id=alert.id,
        action=f"ALERT_ACTION_{act_upper}",
        details={
            "alert_id": alert.id,
            "customer_id": alert.customer_id,
            "previous_status": prev_status,
            "new_status": act_upper,
            "notes": req.notes
        }
    )
    db.add(audit)
    db.commit()

    cust = db.query(Customer).filter(Customer.id == alert.customer_id).first()
    cust_name = cust.name if cust else None

    return AlertResponse(
        id=alert.id,
        customer_id=alert.customer_id,
        customer_name=cust_name,
        trigger_event_id=alert.trigger_event_id,
        previous_tier=alert.previous_tier,
        new_tier=alert.new_tier,
        previous_score=alert.previous_score,
        new_score=alert.new_score,
        previous_log_odds=alert.previous_log_odds,
        new_log_odds=alert.new_log_odds,
        status=alert.status,
        recommended_action=alert.recommended_action,
        breakdown=alert.breakdown,
        created_at=alert.created_at,
        updated_at=alert.updated_at
    )
