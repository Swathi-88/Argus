from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.models import Customer, Event, MaterializedEvent
from app.config import settings
from app.classifier import SignalClassifier

@dataclass
class MaterialityResult:
    is_material: bool
    materiality_score: float
    reasons: List[str]
    previous_risk_score: float
    new_risk_score: float

class MaterialityGate:
    """
    Materiality Gate Function:
    Given a classified event and a matched customer, evaluates whether this event is
    relevant and significant enough to trigger a risk recalculation.
    
    Checks:
    1. Category relevance to customer's risk domain (Individual vs Corporate, High Risk Industry, PEP/Sanction status).
    2. Match confidence score above threshold (default 65.0%).
    3. Event deduplication: Not a duplicate of a recent event for the same customer within time window (e.g., 24 hours).
    """
    def __init__(
        self,
        confidence_threshold: float = settings.MATERIALITY_CONFIDENCE_THRESHOLD,
        dedup_window_hours: int = settings.DEDUPLICATION_WINDOW_HOURS
    ):
        self.confidence_threshold = confidence_threshold
        self.dedup_window_hours = dedup_window_hours
        self.classifier = SignalClassifier()

    def is_category_relevant(self, customer: Customer, category: str, event_type: str, raw_payload: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Evaluates whether the event category is relevant to the customer's risk domain.
        """
        cat_upper = category.upper()
        cust_type_upper = (customer.type or "").upper()
        industry_upper = (customer.industry or "").upper()

        if cat_upper == "SANCTIONS_MATCH":
            return True, "Sanctions/PEP signals are universally relevant to all entity types."

        if cat_upper == "CORPORATE_CHANGE":
            if cust_type_upper == "CORPORATE":
                return True, "Corporate restructuring & filing changes are relevant to Corporate customers."
            else:
                return False, f"Corporate change event is not domain-relevant for Individual customer (ID: {customer.id})."

        if cat_upper == "REGULATORY_CHANGE":
            # Replicating FCA WealthTek client money authorization check
            # Relevant for Corporate entities, high turnover firms, or financial/fintech industries
            is_fintech_or_high_value = (
                cust_type_upper == "CORPORATE" or 
                "BANK" in industry_upper or "FINTECH" in industry_upper or "ASSET" in industry_upper or
                customer.expected_turnover >= 1_000_000
            )
            if is_fintech_or_high_value:
                return True, "Regulatory & FCA client money authorization signals are highly relevant to corporate/financial entity risk."
            return True, "Regulatory change checked for customer entity."

        if cat_upper in ("ADVERSE_MEDIA", "TRANSACTION_ANOMALY"):
            return True, f"{cat_upper} signals are domain-relevant for risk monitoring."

        return True, "Default domain relevance accepted."

    def is_duplicate(self, customer_id: int, category: str, event_type: str, db: Session) -> Tuple[bool, Optional[str]]:
        """
        Checks if a similar event for the same customer was already ingested within the deduplication window.
        """
        cutoff = datetime.now() - timedelta(hours=self.dedup_window_hours)
        
        # Query recent events for this customer
        recent_event = db.query(Event).filter(
            Event.matched_customer_id == customer_id,
            Event.category == category,
            Event.created_at >= cutoff
        ).order_by(Event.created_at.desc()).first()

        if recent_event:
            return True, f"Duplicate event detected for Customer {customer_id} (Category: {category}) within the last {self.dedup_window_hours}h."
        
        return False, None

    def calculate_new_risk(self, current_risk: float, category: str, severity: str) -> float:
        """
        Calculates updated risk score using the Category x Severity weight pair.
        """
        weight = self.classifier.get_severity_weight(category, severity)
        new_score = min(100.0, round(current_risk + weight, 1))
        return new_score

    def evaluate(
        self,
        event: Event,
        customer: Optional[Customer],
        db: Session
    ) -> MaterialityResult:
        """
        Main Materiality Gate evaluation routine.
        """
        reasons = []

        # 0. Unmatched customer check
        if not customer or not event.matched_customer_id:
            reasons.append("Unmatched entity: Event could not be mapped to any customer record with sufficient confidence.")
            return MaterialityResult(
                is_material=False,
                materiality_score=0.0,
                reasons=reasons,
                previous_risk_score=0.0,
                new_risk_score=0.0
            )

        prev_risk = customer.risk_score or 0.0

        # 1. Match Confidence Check
        if event.match_confidence < self.confidence_threshold:
            reasons.append(f"Match confidence ({event.match_confidence:.1f}%) is below materiality threshold ({self.confidence_threshold:.1f}%).")
            return MaterialityResult(
                is_material=False,
                materiality_score=round(event.match_confidence, 2),
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk
            )
        reasons.append(f"Entity match confidence passed ({event.match_confidence:.1f}% >= {self.confidence_threshold:.1f}%).")

        # 2. Domain Relevance Check
        is_relevant, domain_msg = self.is_category_relevant(
            customer=customer,
            category=event.category,
            event_type=event.event_type,
            raw_payload=event.raw_payload
        )
        reasons.append(domain_msg)
        if not is_relevant:
            return MaterialityResult(
                is_material=False,
                materiality_score=0.0,
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk
            )

        # 3. Event Deduplication Check
        # Category-specific window override: SANCTIONS_MATCH has 0h window (always pass through)
        category_dedup_hours = 0 if event.category == "SANCTIONS_MATCH" else self.dedup_window_hours
        
        if category_dedup_hours > 0:
            cutoff = datetime.now() - timedelta(hours=category_dedup_hours)
            existing_dup = db.query(Event).filter(
                Event.matched_customer_id == customer.id,
                Event.category == event.category,
                Event.id != event.id,
                Event.created_at >= cutoff
            ).first()

            if existing_dup:
                reasons.append(f"Suppressed: Duplicate {event.category} event already processed for customer within {category_dedup_hours}h window.")
                return MaterialityResult(
                    is_material=False,
                    materiality_score=0.0,
                    reasons=reasons,
                    previous_risk_score=prev_risk,
                    new_risk_score=prev_risk
                )
            reasons.append(f"Passed deduplication check (no duplicate {event.category} event in past {category_dedup_hours}h).")
        else:
            reasons.append(f"Passed deduplication check (0h deduplication window for {event.category}).")

        # 4. Materiality Score Calculation
        severity_weight = self.classifier.get_severity_weight(event.category, event.severity)
        
        # 5. Minimum Severity / Materiality Threshold Filter
        # Low-severity routine events (routine filings, product announcements) are filtered out
        if event.severity.upper() == "LOW" or event.event_type.upper() in ("ROUTINE_ANNOUNCEMENT", "ROUTINE_FILING"):
            reasons.append(f"Filtered out: Event severity level ({event.severity}) is below minimum threshold for downstream risk escalation.")
            return MaterialityResult(
                is_material=False,
                materiality_score=round((event.match_confidence / 100.0) * severity_weight, 2),
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk
            )

        # Normalize materiality score 0 - 100
        materiality_score = round(min(100.0, (event.match_confidence / 100.0) * severity_weight * 2.0), 2)
        
        new_risk = self.calculate_new_risk(prev_risk, event.category, event.severity)
        reasons.append(f"Material Event Confirmed: Materiality Score = {materiality_score}/100. Risk weight = {severity_weight}. Risk score updated from {prev_risk:.1f} to {new_risk:.1f}.")

        return MaterialityResult(
            is_material=True,
            materiality_score=materiality_score,
            reasons=reasons,
            previous_risk_score=prev_risk,
            new_risk_score=new_risk
        )

