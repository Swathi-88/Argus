import time
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app import audit
from app.database import SessionLocal
from app.models import Event, Customer, MaterializedEvent
from app.entity_resolution import EntityResolver
from app.classifier import SignalClassifier
from app.materiality import MaterialityGate
from app.queue import event_queue
from app.config import settings

logger = logging.getLogger(__name__)

from app.risk_engine import map_probability_to_tier

def determine_risk_tier(score: float) -> str:
    # If score is given as percentage (0-100), convert to probability (0-1)
    prob = score / 100.0 if score > 1.0 else score
    return map_probability_to_tier(prob)


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

    def process_event_payload(
        self,
        payload: Dict[str, Any],
        db: Session,
        actor: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        trace: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[MaterializedEvent]:
        """
        Runs one event through the full pipeline.

        `actor` / `actor_role` attribute the resulting audit records to whoever
        caused the ingestion — a connector poll is SYSTEM, a manual injection
        from the console is the signed-in user.

        `trace`, when supplied, is appended with a per-stage record (name,
        duration, outcome). The console's live pipeline view renders it so the
        demo can show the stages rather than only the end state.
        """
        entity_name = payload.get("entity_name", "").strip()
        event_type = payload.get("event_type", "GENERIC_SIGNAL")
        source = payload.get("source", "External_Queue")
        raw_payload = payload.get("raw_payload", {})

        req_category = payload.get("category")
        req_severity = payload.get("severity")

        def stage(name: str, started: float, status: str, detail: Dict[str, Any]) -> None:
            if trace is not None:
                trace.append(
                    {
                        "stage": name,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "detail": detail,
                    }
                )

        if not entity_name:
            return None

        # 1. Entity Resolution (Phase 1)
        t0 = time.perf_counter()
        resolution = self.resolver.resolve(entity_name, db)
        stage(
            "ENTITY_RESOLUTION",
            t0,
            "MATCHED" if resolution.matched_customer_id else "UNMATCHED",
            {
                "query": entity_name,
                "matched_customer_id": resolution.matched_customer_id,
                "matched_customer_name": resolution.matched_customer_name,
                "matched_string": resolution.matched_string,
                "confidence": resolution.confidence_score,
                "method": resolution.match_method,
            },
        )

        # 2. Signal Classification
        t0 = time.perf_counter()
        category, severity, _ = self.classifier.classify(
            event_type=event_type,
            raw_payload=raw_payload,
            source=source,
            requested_category=req_category,
            requested_severity=req_severity
        )
        stage(
            "CLASSIFICATION",
            t0,
            "CLASSIFIED",
            {"event_type": event_type, "category": category, "severity": severity, "source": source},
        )

        # 3. Persist Raw Event Record
        t0 = time.perf_counter()
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

        # --- Audit: the event exists, before anything is decided about it ---
        audit.record(
            db,
            entity_type="EVENT",
            entity_id=event.id,
            customer_id=resolution.matched_customer_id,
            action="EVENT_INGESTED",
            actor=actor,
            actor_role=actor_role,
            details={
                "entity_name": entity_name,
                "event_type": event_type,
                "category": category,
                "severity": severity,
                "source": source,
            },
        )

        # --- Audit: the entity-resolution outcome, matched or not ---
        audit.record(
            db,
            entity_type="RESOLUTION",
            entity_id=event.id,
            customer_id=resolution.matched_customer_id,
            action="ENTITY_MATCHED" if resolution.matched_customer_id else "ENTITY_MATCH_FAILED",
            actor=actor,
            actor_role=actor_role,
            details={
                "query_string": entity_name,
                "matched_customer_id": resolution.matched_customer_id,
                "matched_customer_name": resolution.matched_customer_name,
                "matched_against": resolution.matched_string,
                "confidence_score": resolution.confidence_score,
                "match_method": resolution.match_method,
            },
        )
        stage("PERSIST_EVENT", t0, "PERSISTED", {"event_id": event.id})

        # 4. Materiality Gate Check
        customer = None
        if resolution.matched_customer_id:
            customer = db.query(Customer).filter(Customer.id == resolution.matched_customer_id).first()

        # The gate scores the customer as part of deciding materiality, so the
        # MATERIALITY_DECISION record has to be written from inside the gate's
        # decision point — otherwise it would land in the chain *after* the
        # RISK_SCORE_UPDATED record it caused, and the trail would read backwards.
        def record_decision(
            is_material: bool,
            materiality_score: float,
            reasons: List[str],
            cause: Optional[str],
        ) -> None:
            audit.record(
                db,
                entity_type="MATERIALITY",
                entity_id=event.id,
                customer_id=resolution.matched_customer_id,
                action="MATERIALITY_DECISION",
                actor=actor,
                actor_role=actor_role,
                details={
                    "decision": "MATERIAL" if is_material else "NOT_MATERIAL",
                    "materiality_score": materiality_score,
                    "suppression_cause": cause,
                    "reasons": reasons,
                    "category": category,
                    "severity": severity,
                    "match_confidence": resolution.confidence_score,
                },
            )

        t0 = time.perf_counter()
        mat_result = self.gate.evaluate(
            event=event,
            customer=customer,
            db=db,
            actor=actor,
            actor_role=actor_role,
            on_decision=record_decision,
        )
        stage(
            "MATERIALITY_GATE",
            t0,
            "MATERIAL" if mat_result.is_material else "SUPPRESSED",
            {
                "is_material": mat_result.is_material,
                "materiality_score": mat_result.materiality_score,
                "reasons": mat_result.reasons,
                "previous_risk_score": mat_result.previous_risk_score,
                "new_risk_score": mat_result.new_risk_score,
            },
        )

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

            # The Bayesian engine has already written customer.risk_score,
            # risk_tier and log_odds inside the gate; re-deriving the tier here
            # keeps the two in step if the gate is ever called standalone.
            customer.risk_score = mat_result.new_risk_score
            customer.risk_tier = determine_risk_tier(mat_result.new_risk_score)
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
