"""
Append-only, hash-chained audit trail.

Every state change in the engine is written here and nowhere else. Two
independent properties are maintained:

*   **Append-only** — enforced by PostgreSQL triggers (app/db_constraints.py).
    No code path, ORM call, or psql session using the application's credentials
    can UPDATE, DELETE, or TRUNCATE a record.

*   **Tamper-evident** — each record stores the SHA-256 hash of its own
    canonical content plus the hash of its predecessor. Editing or removing a
    record at the storage layer (below the triggers — a restored backup, direct
    file access) breaks the chain at a detectable position. `verify_chain()`
    recomputes the whole chain and names the first record that fails.

The two are complementary: the trigger stops the easy attack, the chain detects
the hard one.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger(__name__)

# Genesis value for the first record in the chain.
GENESIS_HASH = "0" * 64

# Arbitrary but fixed key for the advisory lock that serialises chain appends.
_CHAIN_LOCK_KEY = 8_412_776_301

# The canonical action vocabulary. Keeping this closed means the audit trail can
# be queried and grouped reliably, and a typo in a call site fails loudly.
SYSTEM_ACTIONS = {
    "EVENT_INGESTED",
    "ENTITY_MATCHED",
    "ENTITY_MATCH_FAILED",
    "MATERIALITY_DECISION",
    "RISK_SCORE_UPDATED",
    "ALERT_GENERATED",
    "PERIODIC_REVIEW_EXECUTED",
    "EVALUATION_RUN_COMPLETED",
    "SEED_DATABASE",
    "SYSTEM_STARTUP",
}
ANALYST_ACTIONS = {
    "ANALYST_LOGIN",
    "ANALYST_LOGIN_FAILED",
    "ANALYST_ACTION_CONFIRMED",
    "ANALYST_ACTION_DISMISSED",
    "ANALYST_ACTION_ESCALATED",
    "ANALYST_ACTION_INFO_REQUESTED",
    "AUDIT_TRAIL_EXPORTED",
    "AUDIT_CHAIN_VERIFIED",
    "PERMISSION_DENIED",
}
KNOWN_ACTIONS = SYSTEM_ACTIONS | ANALYST_ACTIONS

# Human-readable labels for the UI timeline.
ACTION_LABELS = {
    "EVENT_INGESTED": "Event ingested",
    "ENTITY_MATCHED": "Entity matched to customer",
    "ENTITY_MATCH_FAILED": "Entity match failed",
    "MATERIALITY_DECISION": "Materiality decision",
    "RISK_SCORE_UPDATED": "Risk score recalculated",
    "ALERT_GENERATED": "Alert generated",
    "PERIODIC_REVIEW_EXECUTED": "Periodic review executed",
    "EVALUATION_RUN_COMPLETED": "Evaluation run completed",
    "SEED_DATABASE": "Customer base seeded",
    "SYSTEM_STARTUP": "System started",
    "ANALYST_LOGIN": "Analyst signed in",
    "ANALYST_LOGIN_FAILED": "Failed sign-in attempt",
    "ANALYST_ACTION_CONFIRMED": "Reassessment confirmed by analyst",
    "ANALYST_ACTION_DISMISSED": "Alert dismissed by analyst",
    "ANALYST_ACTION_ESCALATED": "Alert escalated to MLRO",
    "ANALYST_ACTION_INFO_REQUESTED": "Further information requested",
    "AUDIT_TRAIL_EXPORTED": "Audit trail exported",
    "AUDIT_CHAIN_VERIFIED": "Audit chain integrity verified",
    "PERMISSION_DENIED": "Permission denied",
}


def _canonical_json(payload: Any) -> str:
    """
    Deterministic JSON encoding. Sorted keys and fixed separators mean the same
    logical content always hashes to the same digest, on any machine.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_record_hash(
    sequence_no: int,
    entity_type: str,
    entity_id: int,
    customer_id: Optional[int],
    action: str,
    actor: str,
    actor_role: str,
    details: Optional[Dict[str, Any]],
    created_at: datetime,
    prev_hash: str,
) -> str:
    """
    SHA-256 over the record's full canonical content, including its timestamp
    and its predecessor's hash. Any change to any field changes the digest.
    """
    material = _canonical_json(
        {
            "seq": sequence_no,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "customer_id": customer_id,
            "action": action,
            "actor": actor,
            "actor_role": actor_role,
            "details": details,
            # isoformat() rather than the datetime, so re-hashing a row read back
            # out of Postgres produces the same string.
            "created_at": created_at.isoformat(),
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    details: Optional[Dict[str, Any]] = None,
    customer_id: Optional[int] = None,
    actor: str = "SYSTEM",
    actor_role: str = "SYSTEM",
    commit: bool = True,
) -> AuditLog:
    """
    Appends one record to the audit chain.

    Serialised with a transaction-scoped advisory lock: two concurrent workers
    must not read the same tail hash and fork the chain. The lock is released
    automatically when the transaction ends, including on rollback.
    """
    if action not in KNOWN_ACTIONS:
        # Loud, because an unrecognised action means the audit vocabulary and the
        # code have drifted apart and reports built on it will silently miss rows.
        logger.warning("Audit action '%s' is not in the known vocabulary.", action)

    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})

    tail = (
        db.query(AuditLog.sequence_no, AuditLog.record_hash)
        .order_by(AuditLog.sequence_no.desc().nullslast())
        .limit(1)
        .first()
    )
    if tail and tail.sequence_no is not None:
        next_seq = tail.sequence_no + 1
        prev_hash = tail.record_hash or GENESIS_HASH
    else:
        # First chained record. Rows written by earlier phases have no
        # sequence_no; the chain starts cleanly from here and those legacy rows
        # are reported separately as unchained.
        next_seq = 1
        prev_hash = GENESIS_HASH

    # Set explicitly rather than via server_default, so the timestamp is inside
    # the hash rather than assigned after it.
    created_at = datetime.now(timezone.utc)

    entry = AuditLog(
        sequence_no=next_seq,
        entity_type=entity_type,
        entity_id=entity_id,
        customer_id=customer_id,
        action=action,
        details=details or {},
        actor=actor,
        actor_role=actor_role,
        prev_hash=prev_hash,
        created_at=created_at,
    )
    entry.record_hash = compute_record_hash(
        sequence_no=next_seq,
        entity_type=entity_type,
        entity_id=entity_id,
        customer_id=customer_id,
        action=action,
        actor=actor,
        actor_role=actor_role,
        details=details or {},
        created_at=created_at,
        prev_hash=prev_hash,
    )

    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    else:
        db.flush()
    return entry


def verify_chain(db: Session, customer_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Recomputes the hash chain and reports the first break, if any.

    Note the chain is global, so a customer-scoped call still verifies the whole
    chain (a per-customer subset is not itself a chain) and then reports how many
    of the verified records belong to that customer.
    """
    rows: List[AuditLog] = (
        db.query(AuditLog)
        .filter(AuditLog.sequence_no.isnot(None))
        .order_by(AuditLog.sequence_no.asc())
        .all()
    )

    unchained = (
        db.query(AuditLog).filter(AuditLog.sequence_no.is_(None)).count()
    )

    expected_prev = GENESIS_HASH
    expected_seq = 1
    breaks: List[Dict[str, Any]] = []

    for row in rows:
        if row.sequence_no != expected_seq:
            breaks.append(
                {
                    "record_id": row.id,
                    "sequence_no": row.sequence_no,
                    "reason": f"Sequence gap: expected {expected_seq}, found {row.sequence_no}",
                }
            )
            expected_seq = row.sequence_no

        if row.prev_hash != expected_prev:
            breaks.append(
                {
                    "record_id": row.id,
                    "sequence_no": row.sequence_no,
                    "reason": "Predecessor hash does not match the previous record",
                    "expected_prev_hash": expected_prev,
                    "stored_prev_hash": row.prev_hash,
                }
            )

        recomputed = compute_record_hash(
            sequence_no=row.sequence_no,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            customer_id=row.customer_id,
            action=row.action,
            actor=row.actor or "SYSTEM",
            actor_role=row.actor_role or "SYSTEM",
            details=row.details or {},
            created_at=row.created_at,
            prev_hash=row.prev_hash or GENESIS_HASH,
        )
        if recomputed != row.record_hash:
            breaks.append(
                {
                    "record_id": row.id,
                    "sequence_no": row.sequence_no,
                    "reason": "Record content does not match its stored hash — record was altered",
                    "stored_hash": row.record_hash,
                    "recomputed_hash": recomputed,
                }
            )

        expected_prev = row.record_hash or GENESIS_HASH
        expected_seq += 1

    scoped = 0
    if customer_id is not None:
        scoped = sum(1 for r in rows if r.customer_id == customer_id)

    return {
        "chain_valid": len(breaks) == 0,
        "records_verified": len(rows),
        "records_in_scope": scoped if customer_id is not None else len(rows),
        "unchained_legacy_records": unchained,
        "head_hash": rows[-1].record_hash if rows else GENESIS_HASH,
        "head_sequence_no": rows[-1].sequence_no if rows else 0,
        "breaks": breaks,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "SHA-256 over canonical JSON, chained via prev_hash",
    }
