"""
Event ingestion.

Two entry points onto the same pipeline:

*   ``POST /events``        — ordinary ingestion, returns the stored event.
*   ``POST /events/inject`` — the same work, but returns a stage-by-stage trace
    plus every artefact produced (materialized event, alert, audit records).
    This is what the console's live pipeline view renders, so the demo can show
    matching → classification → materiality → risk update → alert → audit
    happening rather than just the end state.
"""
import time
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import P_INGEST_EVENTS, P_VIEW_CUSTOMERS, Principal, require
from app.classifier import SignalClassifier
from app.config import settings
from app.database import get_db
from app.entity_resolution import EntityResolver
from app.models import Alert, AuditLog, Customer, Event, MaterializedEvent
from app.queue import event_queue
from app.routers.alerts import _build_response as build_alert_response
from app.routers.audit import _to_response as audit_to_response
from app.schemas import (
    EventCreate,
    EventListResponse,
    EventResponse,
    PipelineStage,
    PipelineTraceResponse,
)
from app.worker import WorkerProcess

router = APIRouter(prefix="/events", tags=["Events"])

resolver = EntityResolver(fuzzy_threshold=settings.FUZZY_MATCH_THRESHOLD)
classifier = SignalClassifier()
worker = WorkerProcess()


def _run_pipeline(
    event_in: EventCreate,
    db: Session,
    principal: Principal,
    trace: Optional[List[dict]] = None,
) -> Optional[MaterializedEvent]:
    """
    Classifies, publishes to the queue, then drives the worker synchronously.

    The synchronous drive is deliberate for the demo: the same code the
    background worker runs, but the HTTP caller waits for it so the UI can show
    the result immediately. Production would let run_worker_loop consume instead.
    """
    category, severity, _ = classifier.classify(
        event_type=event_in.event_type,
        raw_payload=event_in.raw_payload,
        source=event_in.source,
        requested_category=event_in.category,
        requested_severity=event_in.severity,
    )

    payload = {
        "entity_name": event_in.entity_name,
        "event_type": event_in.event_type,
        "category": category,
        "severity": severity,
        "source": event_in.source,
        "raw_payload": event_in.raw_payload or {},
    }

    event_queue.publish(
        entity_name=event_in.entity_name,
        event_type=event_in.event_type,
        category=category,
        severity=severity,
        source=event_in.source,
        raw_payload=event_in.raw_payload or {},
    )

    return worker.process_event_payload(
        payload,
        db,
        actor=principal.username,
        actor_role=principal.role,
        trace=trace,
    )


def _event_id_from_trace(trace: List[dict]) -> Optional[int]:
    """The worker records the new row's id on its PERSIST_EVENT stage."""
    for stage in trace:
        if stage["stage"] == "PERSIST_EVENT":
            return stage["detail"].get("event_id")
    return None


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def ingest_event(
    event_in: Optional[EventCreate] = Body(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_INGEST_EVENTS)),
):
    """Ingests one event through classification, matching, and the materiality gate."""
    if event_in is None:
        event_in = EventCreate()

    trace: List[dict] = []
    _run_pipeline(event_in, db, principal, trace)

    event_id = _event_id_from_trace(trace)
    # Resolved by id rather than "the newest row", which would race under
    # concurrent ingestion.
    event = db.query(Event).filter(Event.id == event_id).first() if event_id else None
    if not event:
        raise HTTPException(status_code=500, detail="Failed to save event")

    response = EventResponse.model_validate(event)
    if event.matched_customer_id:
        found = db.query(Customer.name).filter(Customer.id == event.matched_customer_id).first()
        response.matched_customer_name = found[0] if found else None
    return response


@router.post(
    "/inject",
    response_model=PipelineTraceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an event and return a stage-by-stage pipeline trace",
)
def inject_event_with_trace(
    event_in: Optional[EventCreate] = Body(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_INGEST_EVENTS)),
):
    if event_in is None:
        event_in = EventCreate()

    # Watermark the audit table so we can report exactly which records this one
    # injection produced.
    audit_watermark = db.query(AuditLog.id).order_by(AuditLog.id.desc()).first()
    audit_floor = audit_watermark[0] if audit_watermark else 0

    trace: List[dict] = []
    started = time.perf_counter()
    mat_event = _run_pipeline(event_in, db, principal, trace)
    total_ms = round((time.perf_counter() - started) * 1000, 2)

    event_id = _event_id_from_trace(trace)
    event = db.query(Event).filter(Event.id == event_id).first() if event_id else None
    if not event:
        raise HTTPException(status_code=500, detail="Failed to save event")

    event_response = EventResponse.model_validate(event)
    customer = None
    if event.matched_customer_id:
        customer = db.query(Customer).filter(Customer.id == event.matched_customer_id).first()
        event_response.matched_customer_name = customer.name if customer else None

    # An alert, if the risk update produced one for this trigger event.
    alert = (
        db.query(Alert)
        .filter(Alert.trigger_event_id == event.id)
        .order_by(Alert.id.desc())
        .first()
    )

    new_audit_records = (
        db.query(AuditLog)
        .filter(AuditLog.id > audit_floor)
        .order_by(AuditLog.id.asc())
        .all()
    )

    if alert:
        trace.append(
            {
                "stage": "ALERT_GENERATION",
                "status": "GENERATED",
                "duration_ms": 0.0,
                "detail": {
                    "alert_id": alert.id,
                    "transition": f"{alert.previous_tier} -> {alert.new_tier}",
                    "recommended_action": alert.recommended_action,
                },
            }
        )
    trace.append(
        {
            "stage": "AUDIT_LOG",
            "status": "WRITTEN",
            "duration_ms": 0.0,
            "detail": {
                "records_written": len(new_audit_records),
                "actions": [r.action for r in new_audit_records],
                "head_hash": new_audit_records[-1].record_hash if new_audit_records else None,
            },
        }
    )

    return PipelineTraceResponse(
        event=event_response,
        stages=[PipelineStage(**s) for s in trace],
        total_duration_ms=total_ms,
        materialized=mat_event is not None,
        materialized_event_id=mat_event.id if mat_event else None,
        alert_generated=alert is not None,
        alert_id=alert.id if alert else None,
        alert=build_alert_response(alert, customer, event) if alert else None,
        audit_records_written=len(new_audit_records),
        audit_records=[audit_to_response(r) for r in new_audit_records],
    )


@router.get("", response_model=EventListResponse)
def list_events(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    severity: Optional[str] = Query(None, description="Filter by event severity"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    matched_customer_id: Optional[int] = Query(None, description="Filter by matched customer ID"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_CUSTOMERS)),
):
    """Retrieves a paginated list of ingested events with optional filtering."""
    query = db.query(Event)

    if severity:
        query = query.filter(Event.severity == severity.upper())
    if event_type:
        query = query.filter(Event.event_type == event_type)
    if matched_customer_id is not None:
        query = query.filter(Event.matched_customer_id == matched_customer_id)

    total = query.count()
    events = query.order_by(Event.created_at.desc()).offset((page - 1) * size).limit(size).all()

    customer_ids = {e.matched_customer_id for e in events if e.matched_customer_id}
    customer_names = {}
    if customer_ids:
        customer_names = {
            c.id: c.name
            for c in db.query(Customer.id, Customer.name)
            .filter(Customer.id.in_(customer_ids))
            .all()
        }

    responses = []
    for e in events:
        resp = EventResponse.model_validate(e)
        resp.matched_customer_name = customer_names.get(e.matched_customer_id)
        responses.append(resp)

    return EventListResponse(total=total, page=page, size=size, events=responses)
