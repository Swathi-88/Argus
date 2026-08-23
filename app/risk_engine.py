import math
from typing import Dict, Any, Tuple, Optional, List
from sqlalchemy.orm import Session

from app.models import Customer, Event, Alert, AuditLog


HIGH_RISK_INDUSTRIES = {
    "Crypto & Digital Assets", "Defense & Aerospace", "Jewelry & Precious Metals",
    "Real Estate", "Weapons & Gaming", "Energy & Commodities"
}

HIGH_RISK_COUNTRIES = {"KY", "VG", "PA", "AE", "IR", "KP", "RU", "SY", "MM"}

# Easily-tunable Likelihood Ratio (LR) Configuration Table
# Evidence weight LR = P(Event | High Risk) / P(Event | Low Risk)
DEFAULT_LIKELIHOOD_RATIOS: Dict[str, float] = {
    # Specific Event Types
    "SANCTIONS_MATCH": 50.0,
    "REGULATORY_REVOKED": 30.0,
    "LAW_ENFORCEMENT": 25.0,
    "PEP_FLAGGED": 10.0,
    "ADVERSE_MEDIA_HIGH": 8.0,
    "TXN_ANOMALY": 6.0,
    "DIRECTOR_CHANGE": 3.0,
    "ADDRESS_CHANGE": 1.2,

    # Fallback Category x Severity mappings
    "SANCTIONS_MATCH:CRITICAL": 50.0,
    "SANCTIONS_MATCH:HIGH": 25.0,
    "SANCTIONS_MATCH:MEDIUM": 10.0,

    "REGULATORY_CHANGE:CRITICAL": 30.0,
    "REGULATORY_CHANGE:HIGH": 15.0,
    "REGULATORY_CHANGE:MEDIUM": 5.0,

    "ADVERSE_MEDIA:CRITICAL": 15.0,
    "ADVERSE_MEDIA:HIGH": 8.0,
    "ADVERSE_MEDIA:MEDIUM": 3.0,

    "TRANSACTION_ANOMALY:CRITICAL": 12.0,
    "TRANSACTION_ANOMALY:HIGH": 6.0,
    "TRANSACTION_ANOMALY:MEDIUM": 3.0,

    "CORPORATE_CHANGE:CRITICAL": 8.0,
    "CORPORATE_CHANGE:HIGH": 5.0,
    "CORPORATE_CHANGE:MEDIUM": 3.0,
    "CORPORATE_CHANGE:LOW": 1.2,

    # Generic Defaults
    "DEFAULT_CRITICAL": 20.0,
    "DEFAULT_HIGH": 8.0,
    "DEFAULT_MEDIUM": 3.0,
    "DEFAULT_LOW": 1.2,
}


def log_odds_to_probability(log_odds: float) -> float:
    """
    Converts log-odds to probability p: p = 1 / (1 + e^(-log_odds))
    """
    return 1.0 / (1.0 + math.exp(-log_odds))


def probability_to_log_odds(prob: float) -> float:
    """
    Converts probability p to log-odds: log_odds = ln(p / (1 - p))
    Clamps probability to avoid division by zero / log(0).
    """
    p = max(0.0001, min(0.9999, prob))
    return math.log(p / (1.0 - p))


def map_probability_to_tier(prob: float) -> str:
    """
    Tier mapping thresholds:
    < 0.2  : LOW
    0.2-0.5: MEDIUM
    0.5-0.8: HIGH
    > 0.8  : CRITICAL
    """
    if prob >= 0.80:
        return "CRITICAL"
    elif prob >= 0.50:
        return "HIGH"
    elif prob >= 0.20:
        return "MEDIUM"
    else:
        return "LOW"


def get_recommended_action(new_tier: str) -> str:
    """
    Returns action recommendation based on target risk tier.
    """
    if new_tier == "CRITICAL":
        return "IMMEDIATE ACTION REQUIRED: Block outgoing transfers, freeze account credentials, and trigger urgent MLRO / Senior Compliance review."
    elif new_tier == "HIGH":
        return "ENHANCED DUE DILIGENCE (EDD): Request updated Source of Wealth/Funds verification and perform manual compliance review within 48 hours."
    elif new_tier == "MEDIUM":
        return "SIMPLIFIED COMPLIANCE REVIEW: Monitor transaction velocity and record compliance notes for periodic review."
    else:
        return "ROUTINE MONITORING: Maintain standard automated transaction monitoring."


class PriorRiskModel:
    """
    Logistic-regression style scoring function for customer onboarding attributes.
    Outputs initial P(high_risk) and prior log-odds.
    """
    def __init__(
        self,
        base_intercept: float = -3.2,
        w_industry: float = 1.2,
        w_country: float = 1.2,
        w_pep: float = 1.8,
        w_sanctioned: float = 3.5,
        w_corporate: float = 0.4,
        w_turnover: float = 0.5
    ):
        self.base_intercept = base_intercept
        self.w_industry = w_industry
        self.w_country = w_country
        self.w_pep = w_pep
        self.w_sanctioned = w_sanctioned
        self.w_corporate = w_corporate
        self.w_turnover = w_turnover

    def calculate_prior(self, customer: Customer) -> Tuple[float, float, Dict[str, Any]]:
        """
        Calculates prior log-odds and prior probability P(high_risk) for a customer.
        Returns: (prior_prob, prior_log_odds, math_breakdown_dict)
        """
        is_high_ind = (customer.industry or "") in HIGH_RISK_INDUSTRIES
        is_high_cty = (customer.country or "") in HIGH_RISK_COUNTRIES
        is_pep = bool(customer.is_pep)
        is_sanctioned = bool(customer.is_sanctioned)
        is_corporate = (customer.type or "").upper() == "CORPORATE"

        # Turnover ratio calculation
        expected_to = customer.expected_turnover or 100_000.0
        actual_to = getattr(customer, "actual_turnover", 0.0) or 0.0
        turnover_ratio = actual_to / max(expected_to, 1_000.0)
        
        turnover_term = 0.0
        if turnover_ratio > 1.0:
            turnover_term = math.log(turnover_ratio)

        # Logit equation
        logit = self.base_intercept
        contributions = {"base_intercept": self.base_intercept}

        if is_high_ind:
            logit += self.w_industry
            contributions["high_risk_industry"] = self.w_industry
        if is_high_cty:
            logit += self.w_country
            contributions["high_risk_country"] = self.w_country
        if is_pep:
            logit += self.w_pep
            contributions["pep_flag"] = self.w_pep
        if is_sanctioned:
            logit += self.w_sanctioned
            contributions["sanctioned_flag"] = self.w_sanctioned
        if is_corporate:
            logit += self.w_corporate
            contributions["corporate_type"] = self.w_corporate
        if turnover_term > 0:
            val = round(self.w_turnover * turnover_term, 4)
            logit += val
            contributions["turnover_excess_ratio"] = val

        prior_log_odds = round(logit, 4)
        prior_prob = round(log_odds_to_probability(prior_log_odds), 4)

        math_breakdown = {
            "equation": "logit = β0 + β_ind*x_ind + β_cty*x_cty + β_pep*x_pep + β_sanc*x_sanc + β_corp*x_corp + β_turnover*x_turnover",
            "intercept": self.base_intercept,
            "contributions": contributions,
            "prior_log_odds": prior_log_odds,
            "prior_probability": prior_prob,
            "formatted_math": f"ln(p / (1-p)) = {prior_log_odds:.4f}  ==>  p = 1 / (1 + e^(-({prior_log_odds:.4f}))) = {prior_prob:.4f} ({prior_prob*100:.2f}%)"
        }

        return prior_prob, prior_log_odds, math_breakdown


class BayesianRiskEngine:
    """
    Core Risk Scoring Engine:
    Manages Bayesian log-odds updates, Likelihood Ratio lookups, Tier boundary detection,
    and Explainable Alert creation.
    """
    def __init__(self, lr_config: Optional[Dict[str, float]] = None):
        self.lr_config = lr_config or DEFAULT_LIKELIHOOD_RATIOS
        self.prior_model = PriorRiskModel()

    def get_likelihood_ratio(self, category: str, severity: str, event_type: str = "") -> Tuple[float, str]:
        """
        Determines the Likelihood Ratio (LR) for an event based on specific event_type,
        category+severity, or generic fallback.
        """
        cat_upper = (category or "").upper()
        sev_upper = (severity or "").upper()
        type_upper = (event_type or "").upper()

        # 1. Direct event_type lookup
        if type_upper in self.lr_config:
            return self.lr_config[type_upper], f"Event type match: {type_upper}"

        # 2. Category:Severity lookup
        cat_sev_key = f"{cat_upper}:{sev_upper}"
        if cat_sev_key in self.lr_config:
            return self.lr_config[cat_sev_key], f"Category x Severity match: {cat_sev_key}"

        # 3. Category direct lookup
        if cat_upper in self.lr_config:
            return self.lr_config[cat_upper], f"Category match: {cat_upper}"

        # 4. Severity fallback
        fallback_key = f"DEFAULT_{sev_upper}"
        if fallback_key in self.lr_config:
            return self.lr_config[fallback_key], f"Severity fallback: {fallback_key}"

        return 2.0, "Generic default LR fallback"

    def calculate_prior(self, customer: Customer) -> Tuple[float, float, Dict[str, Any]]:
        """
        Calculates onboarding prior probability and log-odds.
        """
        return self.prior_model.calculate_prior(customer)

    def update_customer_risk(
        self,
        customer: Customer,
        event: Event,
        db: Session
    ) -> Tuple[float, float, str, str, bool, Dict[str, Any]]:
        """
        Updates customer running log-odds and risk probability upon a new materialized event.
        Checks for tier boundary crossing and generates an explainable Alert record if triggered.
        
        Returns:
            (new_prob, new_log_odds, old_tier, new_tier, tier_crossed, math_explanation)
        """
        # 1. Retrieve or calculate current log-odds
        if customer.log_odds is None or customer.log_odds == 0.0:
            prior_p, prior_lo, _ = self.prior_model.calculate_prior(customer)
            prev_log_odds = prior_lo
            prev_prob = prior_p
        else:
            prev_log_odds = customer.log_odds
            prev_prob = customer.risk_score or log_odds_to_probability(prev_log_odds)

        old_tier = customer.risk_tier or map_probability_to_tier(prev_prob)

        # 2. Lookup Likelihood Ratio & calculate evidence contribution (Δ = ln(LR))
        lr, lr_reason = self.get_likelihood_ratio(event.category, event.severity, event.event_type)
        delta_log_odds = math.log(lr)

        # 3. Update running log-odds and convert to new probability
        new_log_odds = round(prev_log_odds + delta_log_odds, 4)
        new_prob = round(log_odds_to_probability(new_log_odds), 4)
        new_tier = map_probability_to_tier(new_prob)

        tier_crossed = (old_tier != new_tier)

        # 4. Construct human-readable math explanation
        math_explanation = {
            "step_description": "Bayesian Log-Odds Update",
            "previous_risk_score": prev_prob,
            "previous_log_odds": prev_log_odds,
            "previous_tier": old_tier,
            "event_id": event.id,
            "event_category": event.category,
            "event_severity": event.severity,
            "event_type": event.event_type,
            "likelihood_ratio_LR": lr,
            "lr_selection_rule": lr_reason,
            "log_odds_addition_delta": round(delta_log_odds, 4),
            "formula_log_odds": f"log_odds_new = log_odds_prev + ln(LR) = {prev_log_odds:.4f} + ln({lr:.1f}) = {prev_log_odds:.4f} + {delta_log_odds:.4f} = {new_log_odds:.4f}",
            "new_log_odds": new_log_odds,
            "formula_probability": f"P(high_risk) = 1 / (1 + e^(-{new_log_odds:.4f})) = {new_prob:.4f} ({new_prob*100:.2f}%)",
            "new_risk_score": new_prob,
            "new_tier": new_tier,
            "tier_boundary_crossed": tier_crossed,
            "transition_summary": f"Risk Score shifted from {prev_prob:.4f} ({prev_prob*100:.1f}%) [{old_tier}] to {new_prob:.4f} ({new_prob*100:.1f}%) [{new_tier}]"
        }

        # 5. Persist risk state update on customer record
        customer.log_odds = new_log_odds
        customer.risk_score = new_prob
        customer.risk_tier = new_tier

        # 6. If tier boundary crossed or event is HIGH/CRITICAL severity, generate Alert record!
        alert_record = None
        should_create_alert = tier_crossed or (event.severity and event.severity.upper() in ("CRITICAL", "HIGH"))
        if should_create_alert:
            rec_action = get_recommended_action(new_tier)
            alert_record = Alert(
                customer_id=customer.id,
                trigger_event_id=event.id,
                previous_tier=old_tier,
                new_tier=new_tier,
                previous_score=prev_prob,
                new_score=new_prob,
                previous_log_odds=prev_log_odds,
                new_log_odds=new_log_odds,
                status="NEW",
                recommended_action=rec_action,
                breakdown=math_explanation
            )
            db.add(alert_record)
            db.commit()
            db.refresh(alert_record)


            # Log audit entry
            audit_alert = AuditLog(
                entity_type="ALERT",
                entity_id=alert_record.id,
                action="TIER_BOUNDARY_ALERT_CREATED",
                details={
                    "customer_id": customer.id,
                    "customer_name": customer.name,
                    "trigger_event_id": event.id,
                    "transition": f"{old_tier} -> {new_tier}",
                    "previous_score": prev_prob,
                    "new_score": new_prob,
                    "recommended_action": rec_action
                }
            )
            db.add(audit_alert)
            db.commit()

        return new_prob, new_log_odds, old_tier, new_tier, tier_crossed, math_explanation


# Global instance
risk_engine = BayesianRiskEngine()
