from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import P_VIEW_CUSTOMERS, Principal, require
from app.database import get_db
from app.models import MaterializedEvent, Event, Customer
from app.schemas import MaterializedEventResponse, MaterializedEventListResponse

router = APIRouter(prefix="/materialized-events", tags=["Materialized Events"])

@router.get("", response_model=MaterializedEventListResponse)
def list_materialized_events(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    customer_id: Optional[int] = Query(None, description="Filter by customer ID"),
    category: Optional[str] = Query(None, description="Filter by event category"),
    severity: Optional[str] = Query(None, description="Filter by event severity"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    Retrieves a paginated list of material events that passed the Materiality Gate
    and triggered customer risk score updates.
    """
    query = db.query(MaterializedEvent).join(Event, MaterializedEvent.event_id == Event.id).join(Customer, MaterializedEvent.customer_id == Customer.id)

    if customer_id is not None:
        query = query.filter(MaterializedEvent.customer_id == customer_id)
    if category:
        query = query.filter(MaterializedEvent.category == category.upper())
    if severity:
        query = query.filter(MaterializedEvent.severity == severity.upper())

    total = query.count()
    mat_events = query.order_by(MaterializedEvent.created_at.desc()).offset((page - 1) * size).limit(size).all()

    responses = []
    for me in mat_events:
        resp = MaterializedEventResponse.model_validate(me)
        if me.customer:
            resp.matched_customer_name = me.customer.name
        if me.event:
            resp.entity_name = me.event.entity_name
            resp.event_type = me.event.event_type
            resp.source = me.event.source
        responses.append(resp)

    return MaterializedEventListResponse(
        total=total,
        page=page,
        size=size,
        materialized_events=responses
    )

@router.get("/{event_id}", response_model=MaterializedEventResponse)
def get_materialized_event(
    event_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """
    Retrieves detailed breakdown of a single materialized event record.
    """
    me = db.query(MaterializedEvent).filter(MaterializedEvent.id == event_id).first()
    if not me:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Materialized event record not found")

    resp = MaterializedEventResponse.model_validate(me)
    if me.customer:
        resp.matched_customer_name = me.customer.name
    if me.event:
        resp.entity_name = me.event.entity_name
        resp.event_type = me.event.event_type
        resp.source = me.event.source
    return resp
