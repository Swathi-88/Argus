from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import P_INGEST_EVENTS, Principal, require
from app.database import get_db
from app.models import Customer, Event, MaterializedEvent
from app.schemas import TransactionAnomalyRequest, TransactionAnomalyResponse
from app.anomaly_detector import anomaly_detector
from app.materiality import MaterialityGate
from app.config import settings

router = APIRouter(prefix="/anomalies", tags=["Transaction Anomaly Detection"])


@router.post("/detect", response_model=TransactionAnomalyResponse, summary="Detect anomalies in customer transaction stream")
def detect_transaction_anomalies(
    req: TransactionAnomalyRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_INGEST_EVENTS)),
):
    customer = db.query(Customer).filter(Customer.id == req.customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {req.customer_id} not found."
        )

    txn_dicts = [t.model_dump() for t in req.transactions]
    
    # Run IsolationForest anomaly detection
    res = anomaly_detector.detect_anomalies_in_batch(
        customer=customer,
        transactions=txn_dicts,
        db=db,
        auto_trigger_event=True
    )

    # Process created events through Materiality Gate
    gate = MaterialityGate(
        confidence_threshold=settings.MATERIALITY_CONFIDENCE_THRESHOLD,
        dedup_window_hours=0  # Allow immediate processing for anomaly testing
    )
    materialized_count = 0

    for ev_id in res.get("event_ids_created", []):
        event = db.query(Event).filter(Event.id == ev_id).first()
        if event:
            mat_res = gate.evaluate(
                event=event,
                customer=customer,
                db=db,
                actor=principal.username,
                actor_role=principal.role,
            )
            if mat_res.is_material:
                materialized_count += 1
                mat_event = MaterializedEvent(
                    event_id=event.id,
                    customer_id=customer.id,
                    category=event.category,
                    severity=event.severity,
                    materiality_score=mat_res.materiality_score,
                    decision_reasons=mat_res.reasons,
                    triggered_risk_recalculation=True,
                    previous_risk_score=mat_res.previous_risk_score,
                    new_risk_score=mat_res.new_risk_score
                )
                db.add(mat_event)
                db.commit()

    return TransactionAnomalyResponse(
        customer_id=customer.id,
        total_transactions_analyzed=res.get("evaluated_count", 0),
        anomalies_detected=res.get("anomalies_detected", 0),
        anomaly_events_generated=materialized_count,
        details=res.get("anomalies", [])
    )
