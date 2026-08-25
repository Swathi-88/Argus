"""
Reset the demo database to a clean, presentable state.

Run before a demo:

    python -m app.reset_demo            # wipe events/alerts/audit, keep customers
    python -m app.reset_demo --full     # drop everything and reseed from scratch

Why this exists: the audit trail is append-only by design, so there is no way to
tidy it up record by record — that is the whole point of it. The only honest way
to get a clean trail is to drop the table and start a new chain, which is a
deliberate, explicit operation rather than something the app can do to itself.
"""
import argparse
import sys

from sqlalchemy import text

from app import audit
from app.auth import seed_demo_users
from app.database import SessionLocal, engine, init_db_schema
from app.db_constraints import install_audit_immutability, verify_audit_immutability
from app.seed import seed_database, seed_demo_entities

# Ordered child-first so foreign keys never block a delete.
TRANSACTIONAL_TABLES = [
    "materialized_events",
    "alerts",
    "events",
    "evaluation_runs",
]


def clear_transactional_data() -> None:
    """
    Drops events, alerts, and the audit chain, and returns every customer to
    their onboarding prior — so a demo starts from the same place every time.
    """
    with engine.begin() as conn:
        for table in TRANSACTIONAL_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))

        # audit_logs cannot be DELETEd — the trigger refuses it, correctly. The
        # sanctioned way to start a new chain is to drop the table and let
        # init_db_schema rebuild it with its triggers reinstalled.
        conn.execute(text("DROP TABLE IF EXISTS audit_logs CASCADE"))

        # Reset each customer to their onboarding prior. Recomputed by the same
        # PriorRiskModel the seeder uses, rather than a stored copy.
        conn.execute(text("UPDATE customers SET log_odds = NULL"))

    print("[Reset] Cleared events, alerts, materialized events, evaluations, and the audit chain.")

    # Recompute priors through the real model so the reset state is exactly what
    # onboarding would have produced.
    from app.risk_engine import PriorRiskModel, map_probability_to_tier
    from app.models import Customer

    prior_model = PriorRiskModel()
    db = SessionLocal()
    try:
        customers = db.query(Customer).all()
        for customer in customers:
            probability, log_odds, _ = prior_model.calculate_prior(customer)
            customer.risk_score = probability
            customer.log_odds = log_odds
            customer.risk_tier = map_probability_to_tier(probability)
        db.commit()
        print(f"[Reset] Reset {len(customers)} customers to their onboarding priors.")
    finally:
        db.close()


# A representative spread of events, run through the real pipeline so the demo
# opens on a queue that has something in it. Deliberately mixed: material events
# that should alert, control cases that should be suppressed, name variants that
# exercise alias and fuzzy matching, and an off-book entity that should not match.
DEMO_EVENTS = [
    ("WealthTek Ltd", "CLIENT_MONEY_REVOCATION", "FCA_Register"),
    ("Stunt & Co Ltd", "POLICE_RAID", "NewsAPI"),
    ("Meridian Bullion Trading Ltd", "SANCTIONS_UPDATE", "OpenSanctions_API"),
    ("Kestrel Offshore Holdings Ltd", "UBO_CHANGE", "UK_Companies_House"),
    ("Aldgate Crypto Exchange Ltd", "TRANSACTION_SPIKE", "Internal_Transaction_Monitoring"),
    # Alias: the feed names a trading name, not the name on file.
    ("Vertem Asset Management", "FRAUD_ALLEGATION", "NewsAPI"),
    # Normalised match: punctuation and legal-suffix variance.
    ("Meridian Bullion DMCC", "FCA_AUTHORIZATION_CHANGE", "FCA_Register"),
    ("Aldgate Digital", "PEP_LISTING", "OpenSanctions_API"),
    # Control cases — these should be suppressed by the materiality gate.
    ("Northwind Freight Services Ltd", "ROUTINE_FILING", "UK_Companies_House"),
    ("Kestrel Offshore Holdings Ltd", "REGISTERED_ADDRESS_CHANGE", "UK_Companies_House"),
    ("Stunt & Co Ltd", "UNVERIFIED_NEWS", "NewsAPI"),
    # Off-book entity — should not resolve to any customer.
    ("Zzyzx Quantum Nominees Trust PLC", "SANCTIONS_UPDATE", "OpenSanctions_API"),
]


def inject_demo_events() -> None:
    """Runs DEMO_EVENTS through the production worker pipeline."""
    from app.worker import WorkerProcess

    worker = WorkerProcess()
    db = SessionLocal()
    materialised = 0
    try:
        for entity_name, event_type, source in DEMO_EVENTS:
            result = worker.process_event_payload(
                {
                    "entity_name": entity_name,
                    "event_type": event_type,
                    "source": source,
                    "raw_payload": {"origin": "demo_reset"},
                },
                db,
                actor="SYSTEM",
                actor_role="SYSTEM",
            )
            if result is not None:
                materialised += 1
    finally:
        db.close()

    print(
        f"[Reset] Injected {len(DEMO_EVENTS)} events; {materialised} were material "
        f"and rescored their customer."
    )


def drop_everything() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO PUBLIC"))
    print("[Reset] Dropped and recreated the public schema.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the demo database.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Drop every table and reseed the customer base from scratch (slower).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="Leave the alert queue empty instead of injecting the demo events.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Also run the evaluation harness, so the report is populated.",
    )
    args = parser.parse_args()

    scope = "EVERYTHING including the customer base" if args.full else "events, alerts, and the audit trail"
    if not args.yes:
        print(f"This will permanently delete {scope}.")
        if input("Type 'reset' to continue: ").strip() != "reset":
            print("Aborted.")
            return 1

    if args.full:
        drop_everything()
    else:
        clear_transactional_data()

    init_db_schema()
    install_audit_immutability(engine)

    db = SessionLocal()
    try:
        seed_database(db, target_count=2000)
        seed_demo_entities(db)
        seed_demo_users(db)

        proof = verify_audit_immutability(engine)
        audit.record(
            db,
            entity_type="SYSTEM",
            entity_id=0,
            action="SYSTEM_STARTUP",
            details={"reason": "demo_reset", "scope": scope, "immutability_enforced": proof["enforced"]},
        )
        print(f"[Reset] New audit chain started. Append-only enforced: {proof['enforced']}")
    finally:
        db.close()

    if not args.no_events:
        inject_demo_events()

    if args.evaluate:
        from app.evaluation import run_evaluation

        db = SessionLocal()
        try:
            record = run_evaluation(db)
            engine_metrics = record.metrics["engine"]
            baseline_metrics = record.metrics["baseline"]
            print(
                f"[Reset] Evaluation run #{record.id} in {record.runtime_seconds:.1f}s — "
                f"engine recall {engine_metrics['detection_rate_recall']:.3f} "
                f"vs baseline {baseline_metrics['detection_rate_recall']:.3f}"
            )
        finally:
            db.close()

    print("[Reset] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
