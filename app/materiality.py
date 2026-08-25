from datetime import datetime, timedelta
from typing import Callable, List, Tuple, Optional, Dict, Any
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
    # Populated only when the event was material and scored. Lets a caller
    # distinguish "reassessed" from "reassessed and alerted" without re-deriving
    # the alert rule.
    tier_crossed: bool = False
    alert_generated: bool = False
    previous_tier: Optional[str] = None
    new_tier: Optional[str] = None
    log_odds_delta: Optional[float] = None
    likelihood_ratio: Optional[float] = None
    # Short machine-readable cause when is_material is False, for error analysis.
    suppression_cause: Optional[str] = None

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
        dedup_window_hours: int = settings.DEDUPLICATION_WINDOW_HOURS,
        duplicate_lookup: Optional[Callable[[int, str, datetime, int], bool]] = None,
    ):
        self.confidence_threshold = confidence_threshold
        self.dedup_window_hours = dedup_window_hours
        self.classifier = SignalClassifier()
        # Injectable so the deduplication check can be answered from an
        # in-memory index instead of a query. The evaluation harness supplies
        # one; production leaves it None and the DB query below is used. Signature:
        # (customer_id, category, cutoff, exclude_event_id) -> bool.
        self.duplicate_lookup = duplicate_lookup

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
        db: Session,
        actor: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        persist: bool = True,
        on_decision: Optional[Callable[[bool, float, List[str], Optional[str]], None]] = None,
    ) -> MaterialityResult:
        """
        Main Materiality Gate evaluation routine.

        `actor` / `actor_role` are threaded down to the risk engine so the
        RISK_SCORE_UPDATED and ALERT_GENERATED audit records name whoever caused
        the ingestion.

        `on_decision` is invoked the moment materiality has been decided and
        *before* any scoring happens, with
        (is_material, materiality_score, reasons, suppression_cause).
        The worker uses it to write the MATERIALITY_DECISION audit record at the
        right point in the chain — recording the decision after the score it
        caused would make the audit trail read backwards.
        """
        reasons = []

        def decided(
            is_material: bool,
            materiality_score: float,
            cause: Optional[str] = None,
        ) -> None:
            if on_decision is not None:
                on_decision(is_material, materiality_score, list(reasons), cause)

        # 0. Unmatched customer check
        if not customer or not event.matched_customer_id:
            reasons.append("Unmatched entity: Event could not be mapped to any customer record with sufficient confidence.")
            decided(False, 0.0, "UNMATCHED_ENTITY")
            return MaterialityResult(
                is_material=False,
                materiality_score=0.0,
                reasons=reasons,
                previous_risk_score=0.0,
                new_risk_score=0.0,
                suppression_cause="UNMATCHED_ENTITY",
            )

        prev_risk = customer.risk_score or 0.0

        # 1. Match Confidence Check
        if event.match_confidence < self.confidence_threshold:
            reasons.append(f"Match confidence ({event.match_confidence:.1f}%) is below materiality threshold ({self.confidence_threshold:.1f}%).")
            decided(False, round(event.match_confidence, 2), "LOW_MATCH_CONFIDENCE")
            return MaterialityResult(
                is_material=False,
                materiality_score=round(event.match_confidence, 2),
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk,
                suppression_cause="LOW_MATCH_CONFIDENCE",
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
            decided(False, 0.0, "CATEGORY_NOT_DOMAIN_RELEVANT")
            return MaterialityResult(
                is_material=False,
                materiality_score=0.0,
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk,
                suppression_cause="CATEGORY_NOT_DOMAIN_RELEVANT",
            )

        # 3. Event Deduplication Check
        # Category-specific window override: SANCTIONS_MATCH has 0h window (always pass through)
        category_dedup_hours = 0 if event.category == "SANCTIONS_MATCH" else self.dedup_window_hours
        
        if category_dedup_hours > 0:
            # Anchored to the event's own timestamp, not wall clock, so replaying
            # a historical stream deduplicates against its own timeline.
            reference = event.created_at or datetime.now()
            cutoff = reference - timedelta(hours=category_dedup_hours)

            if self.duplicate_lookup is not None:
                existing_dup = self.duplicate_lookup(
                    customer.id, event.category, cutoff, event.id or -1
                )
            else:
                existing_dup = db.query(Event).filter(
                    Event.matched_customer_id == customer.id,
                    Event.category == event.category,
                    Event.id != event.id,
                    Event.created_at >= cutoff
                ).first()

            if existing_dup:
                reasons.append(f"Suppressed: Duplicate {event.category} event already processed for customer within {category_dedup_hours}h window.")
                decided(False, 0.0, "DEDUPLICATED")
                return MaterialityResult(
                    is_material=False,
                    materiality_score=0.0,
                    reasons=reasons,
                    previous_risk_score=prev_risk,
                    new_risk_score=prev_risk,
                    suppression_cause="DEDUPLICATED",
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
            decided(False, round((event.match_confidence / 100.0) * severity_weight, 2), "BELOW_SEVERITY_THRESHOLD")
            return MaterialityResult(
                is_material=False,
                materiality_score=round((event.match_confidence / 100.0) * severity_weight, 2),
                reasons=reasons,
                previous_risk_score=prev_risk,
                new_risk_score=prev_risk,
                suppression_cause="BELOW_SEVERITY_THRESHOLD",
            )

        # Normalize materiality score 0 - 100
        materiality_score = round(min(100.0, (event.match_confidence / 100.0) * severity_weight * 2.0), 2)
        
        # The event is material. Record that decision before scoring, so the
        # audit chain reads decision-then-consequence.
        decided(True, materiality_score, None)

        # Calculate updated risk using Bayesian Risk Engine
        from app.risk_engine import risk_engine
        new_risk, new_lo, old_tier, new_tier, tier_crossed, math_expl = risk_engine.update_customer_risk(
            customer=customer,
            event=event,
            db=db,
            actor=actor,
            actor_role=actor_role,
            persist=persist,
        )

        reasons.append(
            f"Material Event Confirmed: Materiality Score = {materiality_score}/100. "
            f"Bayesian LR = {math_expl['likelihood_ratio_LR']} (Δ log-odds = +{math_expl['log_odds_addition_delta']:.4f}). "
            f"Risk score updated from {prev_risk:.4f} [{old_tier}] to {new_risk:.4f} [{new_tier}]."
        )
        if tier_crossed:
            reasons.append(f"TIER BOUNDARY ALERT GENERATED: Customer crossed tier boundary from {old_tier} to {new_tier}.")

        return MaterialityResult(
            is_material=True,
            materiality_score=materiality_score,
            reasons=reasons,
            previous_risk_score=prev_risk,
            new_risk_score=new_risk,
            tier_crossed=tier_crossed,
            alert_generated=bool(math_expl.get("alert_generated")),
            previous_tier=old_tier,
            new_tier=new_tier,
            log_odds_delta=math_expl.get("log_odds_addition_delta"),
            likelihood_ratio=math_expl.get("likelihood_ratio_LR"),
        )


