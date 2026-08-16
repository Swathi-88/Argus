import time
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Event, Customer, MaterializedEvent, AuditLog
from app.entity_resolution import EntityResolver
from app.classifier import SignalClassifier
from app.materiality import MaterialityGate
from app.queue import event_queue
from app.config import settings

logger = logging.getLogger(__name__)

def determine_risk_tier(score: float) -> str:
    if score >= 80.0:
        return "CRITICAL"
    elif score >= 60.0:
        return "HIGH"
    elif score >= 35.0:
        return "MEDIUM"
    else:
        return "LOW"

class WorkerProcess:
    """
    Async Worker Process:
    Picks events from Redis Stream Event Queue, executes:
    1. Entity Resolution (Phase 1)
    2. Signal Classification (Category & Severity Weighting)
    3. Materiality Gate Check (Relevance, Confidence, Deduplication)
    4. Database Persistence (Events, MaterializedEvents, AuditLog, Risk Recalculation)
    """
    def __init__(self):
        self.resolver = EntityResolver(fuzzy_threshold=settings.FUZZY_MATCH_THRESHOLD)
        self.classifier = SignalClassifier()
        self.gate = MaterialityGate(
            confidence_threshold=settings.MATERIALITY_CONFIDENCE_THRESHOLD,
            dedup_window_hours=settings.DEDUPLICATION_WINDOW_HOURS
        )

    def process_event_payload(self, payload: Dict[str, Any], db: Session) -> Optional[MaterializedEvent]:
        entity_name = payload.get("entity_name", "").strip()
        event_type = payload.get("event_type", "GENERIC_SIGNAL")
        source = payload.get("source", "External_Queue")
        raw_payload = payload.get("raw_payload", {})
        
        req_category = payload.get("category")
        req_severity = payload.get("severity")

        if not entity_name:
            return None

        # 1. Entity Resolution (Phase 1)
        resolution = self.resolver.resolve(entity_name, db)

        # 2. Signal Classification
        category, severity, _ = self.classifier.classify(
            event_type=event_type,
            raw_payload=raw_payload,
            source=source,
            requested_category=req_category,
            requested_severity=req_severity
        )

        # 3. Persist Raw Event Record
        event = Event(
            entity_name=entity_name,
            event_type=event_type,
            category=category,
            severity=severity,
            source=source,
            raw_payload=raw_payload,
            matched_customer_id=resolution.matched_customer_id,
            match_confidence=resolution.confidence_score,
            match_method=resolution.match_method
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        # 4. Materiality Gate Check
        customer = None
        if resolution.matched_customer_id:
            customer = db.query(Customer).filter(Customer.id == resolution.matched_customer_id).first()

        mat_result = self.gate.evaluate(event=event, customer=customer, db=db)

        # Audit log for event ingestion
        audit_ingest = AuditLog(
            entity_type="EVENT",
            entity_id=event.id,
            action="EVENT_INGESTED_AND_CLASSIFIED",
            details={
                "entity_name": entity_name,
                "event_type": event_type,
                "category": category,
                "severity": severity,
                "matched_customer_id": resolution.matched_customer_id,
                "confidence_score": resolution.confidence_score,
                "is_material": mat_result.is_material
            }
        )
        db.add(audit_ingest)
        db.commit()

        # 5. Handle Material Events
        if mat_result.is_material and customer:
            mat_event = MaterializedEvent(
                event_id=event.id,
                customer_id=customer.id,
                category=category,
                severity=severity,
                materiality_score=mat_result.materiality_score,
                decision_reasons=mat_result.reasons,
                triggered_risk_recalculation=True,
                previous_risk_score=mat_result.previous_risk_score,
                new_risk_score=mat_result.new_risk_score
            )
            db.add(mat_event)
            
            # Recalculate customer risk score and tier
            customer.risk_score = mat_result.new_risk_score
            customer.risk_tier = determine_risk_tier(mat_result.new_risk_score)
            
            # Audit log for materiality trigger
            audit_mat = AuditLog(
                entity_type="MATERIALITY",
                entity_id=event.id,
                action="EVENT_MATERIALIZED_RISK_UPDATED",
                details={
                    "customer_id": customer.id,
                    "customer_name": customer.name,
                    "previous_risk": mat_result.previous_risk_score,
                    "new_risk": mat_result.new_risk_score,
                    "new_tier": customer.risk_tier,
                    "materiality_score": mat_result.materiality_score
                }
            )
            db.add(audit_mat)
            db.commit()
            db.refresh(mat_event)
            return mat_event

        return None

    def process_batch(self, batch_size: int = 10) -> List[Optional[MaterializedEvent]]:
        msgs = event_queue.consume(count=batch_size)
        if not msgs:
            return []

        db = SessionLocal()
        materialized_list = []
        try:
            for msg_id, payload in msgs:
                mat_event = self.process_event_payload(payload, db)
                materialized_list.append(mat_event)
                event_queue.ack(msg_id)
        finally:
            db.close()

        return materialized_list


def run_worker_loop(interval_seconds: float = 1.0):
    """
    Continuous worker loop for background execution.
    """
    worker = WorkerProcess()
    print("[Worker] Risk Trigger Event Worker started listening to queue...")
    while True:
        try:
            processed = worker.process_batch(batch_size=10)
            if processed:
                mat_count = sum(1 for m in processed if m is not None)
                print(f"[Worker] Processed {len(processed)} events from queue ({mat_count} material events created).")
        except Exception as e:
            print(f"[Worker] Error processing queue batch: {e}")
        time.sleep(interval_seconds)
