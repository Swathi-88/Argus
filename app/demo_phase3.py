import sys
import math
from datetime import datetime, date
from sqlalchemy.orm import Session

from app.database import engine, Base, SessionLocal
from app.models import Customer, Event, MaterializedEvent, Alert, AuditLog
from app.risk_engine import risk_engine, PriorRiskModel, log_odds_to_probability, map_probability_to_tier, get_recommended_action
from app.materiality import MaterialityGate


def run_phase3_demo():
    print("=" * 80)
    print(" BAYESIAN DYNAMIC RISK TRIGGER ENGINE - PHASE 3 DEMONSTRATION")
    print("=" * 80)

    # 1. Initialize Database
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    try:
        # 2. Setup Test Customer starting at LOW risk
        customer_name = "Apex Global Trading Ltd"
        existing_cust = db.query(Customer).filter(Customer.name == customer_name).first()
        if existing_cust:
            # Clean up old demo records
            db.query(Alert).filter(Alert.customer_id == existing_cust.id).delete()
            db.query(MaterializedEvent).filter(MaterializedEvent.customer_id == existing_cust.id).delete()
            db.query(Event).filter(Event.matched_customer_id == existing_cust.id).delete()
            db.delete(existing_cust)
            db.commit()

        prior_model = PriorRiskModel()
        demo_customer = Customer(
            name=customer_name,
            type="Corporate",
            country="GB",
            industry="Logistics & Shipping",
            expected_turnover=1_500_000.0,
            actual_turnover=1_500_000.0,
            is_pep=False,
            is_sanctioned=False,
            onboarding_date=date(2025, 1, 15)
        )

        prior_p, prior_lo, prior_math = prior_model.calculate_prior(demo_customer)
        prior_tier = map_probability_to_tier(prior_p)

        demo_customer.risk_score = prior_p
        demo_customer.log_odds = prior_lo
        demo_customer.risk_tier = prior_tier

        db.add(demo_customer)
        db.commit()
        db.refresh(demo_customer)

        print("\n" + "-" * 80)
        print(f"STEP 0: CUSTOMER ONBOARDING PRIOR RISK SCORING")
        print(f"-" * 80)
        print(f"Customer Name        : {demo_customer.name} (ID: {demo_customer.id})")
        print(f"Entity Type          : {demo_customer.type} | Country: {demo_customer.country} | Industry: {demo_customer.industry}")
        print(f"Turnover (Exp vs Act): GBP {demo_customer.expected_turnover:,.2f} vs GBP {demo_customer.actual_turnover:,.2f}")
        print(f"PEP Flag: {demo_customer.is_pep} | Sanction Flag: {demo_customer.is_sanctioned}")
        print(f"Intercept (beta0)    : {prior_math['intercept']}")
        print(f"Prior Log-Odds       : {prior_lo:.4f}")
        print(f"Prior Probability P  : {prior_p:.4f} ({prior_p*100:.2f}%)")
        print(f"Initial Risk Tier    : [{prior_tier}]")
        print(f"Math Equation        : {prior_math['formatted_math']}")


        # 3. Define Sequence of Test Events to Inject
        test_events_spec = [
            {
                "event_type": "ADDRESS_CHANGE",
                "category": "CORPORATE_CHANGE",
                "severity": "LOW",
                "source": "Companies_House_API",
                "entity_name": "Apex Global Trading Ltd",
                "description": "Registered address updated to secondary location"
            },
            {
                "event_type": "DIRECTOR_CHANGE",
                "category": "CORPORATE_CHANGE",
                "severity": "MEDIUM",
                "source": "Companies_House_API",
                "entity_name": "Apex Global Trading Ltd",
                "description": "New foreign director appointed to board"
            },
            {
                "event_type": "TXN_ANOMALY",
                "category": "TRANSACTION_ANOMALY",
                "severity": "HIGH",
                "source": "IsolationForest_Anomaly_Engine",
                "entity_name": "Apex Global Trading Ltd",
                "description": "Unusual volume spike & rapid velocity transfer to UAE account"
            },
            {
                "event_type": "ADVERSE_MEDIA_HIGH",
                "category": "ADVERSE_MEDIA",
                "severity": "HIGH",
                "source": "NewsAPI_Monitor",
                "entity_name": "Apex Global Trading Ltd",
                "description": "Regulatory inquiry reported regarding trade mis-invoicing"
            },
            {
                "event_type": "SANCTIONS_MATCH",
                "category": "SANCTIONS_MATCH",
                "severity": "CRITICAL",
                "source": "OpenSanctions_API",
                "entity_name": "Apex Global Trading Ltd",
                "description": "Ultimate beneficial owner placed on UK/EU Sanctions list"
            }
        ]

        gate = MaterialityGate(confidence_threshold=60.0, dedup_window_hours=0)

        print("\n" + "=" * 80)
        print(" INJECTING SEQUENTIAL EVENT STREAM & UPDATING BAYESIAN LOG-ODDS")
        print("=" * 80)

        for step, spec in enumerate(test_events_spec, start=1):
            prev_lo = demo_customer.log_odds
            prev_p = demo_customer.risk_score
            prev_tier = demo_customer.risk_tier

            # Create Raw Event
            ev = Event(
                entity_name=spec["entity_name"],
                event_type=spec["event_type"],
                category=spec["category"],
                severity=spec["severity"],
                source=spec["source"],
                raw_payload={"description": spec["description"]},
                matched_customer_id=demo_customer.id,
                match_confidence=100.0,
                match_method="EXACT"
            )
            db.add(ev)
            db.commit()
            db.refresh(ev)

            # Evaluate through Materiality Gate
            mat_res = gate.evaluate(event=ev, customer=demo_customer, db=db)

            if mat_res.is_material:
                # Store Materialized Event
                me = MaterializedEvent(
                    event_id=ev.id,
                    customer_id=demo_customer.id,
                    category=ev.category,
                    severity=ev.severity,
                    materiality_score=mat_res.materiality_score,
                    decision_reasons=mat_res.reasons,
                    triggered_risk_recalculation=True,
                    previous_risk_score=prev_p,
                    new_risk_score=demo_customer.risk_score
                )
                db.add(me)
                db.commit()

            # Refresh customer state from DB
            db.refresh(demo_customer)
            new_lo = demo_customer.log_odds
            new_p = demo_customer.risk_score
            new_tier = demo_customer.risk_tier

            lr, lr_rule = risk_engine.get_likelihood_ratio(ev.category, ev.severity, ev.event_type)
            delta_lo = math.log(lr)
            tier_boundary_crossed = (prev_tier != new_tier)

            print(f"\n[STEP {step}] EVENT INJECTED: {spec['event_type']} ({spec['category']} : {spec['severity']})")
            print(f" Description          : {spec['description']}")
            print(f" Evidence Weight (LR) : {lr} ({lr_rule})")
            print(f" Log-Odds Addition    : +{delta_lo:.4f}  [ln({lr:.1f})]")
            print(f" Log-Odds Transition  : {prev_lo:.4f} + {delta_lo:.4f} ==> {new_lo:.4f}")
            print(f" Probability Shift    : P(high_risk) = {prev_p:.4f} ({prev_p*100:.2f}%) ==> {new_p:.4f} ({new_p*100:.2f}%)")
            print(f" Tier Transition      : [{prev_tier}] ===> [{new_tier}]")
            
            if tier_boundary_crossed:
                print(f" [ALERT GENERATED] Tier boundary crossed from {prev_tier} to {new_tier}.")
            else:
                print(f" [INFO] Score updated within tier [{new_tier}].")


        # 4. Display Generated Alerts Queue & Explanations
        alerts = db.query(Alert).filter(Alert.customer_id == demo_customer.id).order_by(Alert.id.asc()).all()

        print("\n" + "=" * 80)
        print(f" GENERATED TIER BOUNDARY ALERTS QUEUE ({len(alerts)} ALERTS GENERATED)")
        print("=" * 80)

        for alert in alerts:
            print(f"\nALERT #{alert.id} - Customer {demo_customer.name} (ID: {alert.customer_id})")
            print(f"  Status             : {alert.status}")
            print(f"  Boundary Crossed   : {alert.previous_tier} ===> {alert.new_tier}")
            print(f"  Previous Score/LO  : P = {alert.previous_score:.4f} | log-odds = {alert.previous_log_odds:.4f}")
            print(f"  New Score/LO       : P = {alert.new_score:.4f} | log-odds = {alert.new_log_odds:.4f}")
            print(f"  Recommended Action : {alert.recommended_action}")
            print(f"  Math Breakdown Log :")
            bd = alert.breakdown or {}
            print(f"    - Rule           : {bd.get('lr_selection_rule')}")
            print(f"    - Formula (LO)   : {bd.get('formula_log_odds')}")
            print(f"    - Formula (Prob) : {bd.get('formula_probability')}")
            print(f"    - Summary        : {bd.get('transition_summary')}")

        print("\n" + "=" * 80)
        print(" PHASE 3 DEMONSTRATION COMPLETED SUCCESSFULLY")
        print("=" * 80)

    finally:
        db.close()


if __name__ == "__main__":
    run_phase3_demo()
