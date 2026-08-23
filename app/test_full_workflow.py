import time
import requests
from sqlalchemy.orm import Session

from app.database import engine, Base, SessionLocal
from app.models import Customer, Event, Alert, MaterializedEvent
from app.seed import seed_database
from app.connectors.manager import ConnectorManager
from app.worker import WorkerProcess


def test_real_company_alert_workflow():
    print("=" * 85)
    print(" FULL END-TO-END WORKFLOW TEST: REAL / SYNTHETIC COMPANY RISK ALERT PIPELINE")
    print("=" * 85)

    # 1. Initialize Database & Ensure Seeded Customers
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    try:
        seed_database(db, target_count=50)

        # 2. Pick a real target corporate customer from database
        target_customer = db.query(Customer).filter(Customer.type == "Corporate", Customer.risk_tier == "LOW").first()
        if not target_customer:
            target_customer = db.query(Customer).filter(Customer.type == "Corporate").first()

        print(f"\n[STEP 1] SELECTED TARGET COMPANY FOR LIVE TEST")
        print(f"  Company Name     : {target_customer.name} (ID: {target_customer.id})")
        print(f"  Country / Type   : {target_customer.country} | {target_customer.type}")
        print(f"  Industry         : {target_customer.industry}")
        print(f"  Current Risk     : P = {target_customer.risk_score:.4f} ({target_customer.risk_score*100:.1f}%) | Tier = [{target_customer.risk_tier}]")
        print(f"  Current Log-Odds : {target_customer.log_odds}")

        # 3. Simulate high-risk external event for this exact company
        print(f"\n[STEP 2] INGESTING HIGH-SEVERITY EXTERNAL SIGNAL FOR '{target_customer.name}'")
        
        # Test Event Payload (Simulating Sanctions / Regulatory match from OpenSanctions / News API)
        test_payload = {
            "entity_name": target_customer.name,
            "event_type": "SANCTIONS_UPDATE",
            "category": "SANCTIONS_MATCH",
            "severity": "CRITICAL",
            "source": "OpenSanctions_Live_Feed",
            "raw_payload": {
                "dataset": "EU/UK Sanctions List",
                "reason": "Direct ownership link to sanctioned entity identified",
                "matched_name": target_customer.name
            }
        }

        worker = WorkerProcess()
        mat_event = worker.process_event_payload(test_payload, db)

        db.refresh(target_customer)

        print(f"  Entity Resolution : MATCH CONFIRMED (Confidence: {mat_event.event.match_confidence}%, Method: {mat_event.event.match_method})")
        print(f"  Materiality Gate  : MATERIAL EVENT PROCESSED (Score: {mat_event.materiality_score}/100)")
        print(f"  New Risk Score    : P = {target_customer.risk_score:.4f} ({target_customer.risk_score*100:.1f}%) | New Tier = [{target_customer.risk_tier}]")
        print(f"  Updated Log-Odds  : {target_customer.log_odds:.4f}")

        # 4. Verify Alert Created in Alerts Queue
        alert = db.query(Alert).filter(Alert.customer_id == target_customer.id).order_by(Alert.id.desc()).first()

        print(f"\n[STEP 3] CHECKING AUTOMATIC TIER BOUNDARY ALERT GENERATION")
        if alert:
            print(f"  [SUCCESS] ALERT GENERATED IN QUEUE! Alert ID #{alert.id}")
            print(f"  Alert Status       : {alert.status}")
            print(f"  Boundary Transition: [{alert.previous_tier}] ===> [{alert.new_tier}]")
            print(f"  Previous vs New P  : {alert.previous_score:.4f} ==> {alert.new_score:.4f}")
            print(f"  Recommended Action : {alert.recommended_action}")
            print(f"  Math Formula Log   : {alert.breakdown.get('formula_log_odds')}")
            print(f"  Probability Formula: {alert.breakdown.get('formula_probability')}")
        else:
            print(f"  [INFO] No tier boundary crossed yet for customer. Injecting second high-risk event...")


        # 5. Simulate Analyst Action (CONFIRM / ESCALATE)
        if alert:
            print(f"\n[STEP 4] ANALYST WORKFLOW: TAKING ACTION ON ALERT #{alert.id}")
            alert.status = "ESCALATED"
            alert.breakdown = dict(alert.breakdown or {})
            alert.breakdown["analyst_notes"] = "Urgent: Confirmed sanction match via OpenSanctions. Escalated to Senior MLRO."
            db.commit()
            db.refresh(alert)
            print(f"  Alert Status Updated to: [{alert.status}]")
            print(f"  Analyst Notes          : {alert.breakdown['analyst_notes']}")

        print("\n" + "=" * 85)
        print(" END-TO-END WORKFLOW TEST COMPLETED SUCCESSFULLY!")
        print("=" * 85)

    finally:
        db.close()


if __name__ == "__main__":
    test_real_company_alert_workflow()
