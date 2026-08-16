from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, Customer, AuditLog
from app.schemas import EventCreate, EventResponse, EventListResponse
from app.entity_resolution import EntityResolver
from app.config import settings

from app.classifier import SignalClassifier
from app.queue import event_queue
from app.worker import WorkerProcess

router = APIRouter(prefix="/events", tags=["Events"])

resolver = EntityResolver(fuzzy_threshold=settings.FUZZY_MATCH_THRESHOLD)
classifier = SignalClassifier()
worker = WorkerProcess()


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def ingest_event(event_in: EventCreate, db: Session = Depends(get_db)):
    """
    Ingests a raw incoming event into the Redis Streams Event Queue,
    executes Signal Classification, Entity Resolution, and Materiality Gate processing.
    """
    # 1. Classify Signal
    category, severity, _ = classifier.classify(
        event_type=event_in.event_type,
        raw_payload=event_in.raw_payload,
        source=event_in.source,
        requested_category=event_in.category,
        requested_severity=event_in.severity
    )

    # 2. Push event payload into Redis Streams event queue
    payload = {
        "entity_name": event_in.entity_name,
        "event_type": event_in.event_type,
        "category": category,
        "severity": severity,
        "source": event_in.source,
        "raw_payload": event_in.raw_payload or {}
    }
    msg_id = event_queue.publish(
        entity_name=event_in.entity_name,
        event_type=event_in.event_type,
        category=category,
        severity=severity,
        source=event_in.source,
        raw_payload=event_in.raw_payload or {}
    )

    # 3. Worker process picks up event payload and executes full pipeline
    worker.process_event_payload(payload, db)

    # 4. Fetch the created event from DB to return structured response
    event = db.query(Event).order_by(Event.id.desc()).first()
    if not event:
        raise HTTPException(status_code=500, detail="Failed to save event")

    cust_name = None
    if event.matched_customer_id:
        cust = db.query(Customer.name).filter(Customer.id == event.matched_customer_id).first()
        if cust:
            cust_name = cust[0]

    response = EventResponse.model_validate(event)
    response.matched_customer_name = cust_name
    return response



@router.get("", response_model=EventListResponse)
def list_events(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    severity: Optional[str] = Query(None, description="Filter by event severity (LOW, MEDIUM, HIGH, CRITICAL)"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    matched_customer_id: Optional[int] = Query(None, description="Filter by matched customer ID"),
    db: Session = Depends(get_db)
):
    """
    Retrieves a paginated list of ingested events with optional filtering options.
    """
    query = db.query(Event)

    if severity:
        query = query.filter(Event.severity == severity.upper())
    if event_type:
        query = query.filter(Event.event_type == event_type)
    if matched_customer_id is not None:
        query = query.filter(Event.matched_customer_id == matched_customer_id)

    total = query.count()
    events = query.order_by(Event.created_at.desc()).offset((page - 1) * size).limit(size).all()

    # Map matched_customer_name for events
    customer_ids = [e.matched_customer_id for e in events if e.matched_customer_id]
    customer_names = {}
    if customer_ids:
        cust_records = db.query(Customer.id, Customer.name).filter(Customer.id.in_(customer_ids)).all()
        customer_names = {c.id: c.name for c in cust_records}

    event_responses = []
    for e in events:
        resp = EventResponse.model_validate(e)
        resp.matched_customer_name = customer_names.get(e.matched_customer_id)
        event_responses.append(resp)

    return EventListResponse(
        total=total,
        page=page,
        size=size,
        events=event_responses
    )
