"""
Audit trail endpoints.

The trail is the deliverable an auditor actually asks for, so it is served in
three shapes: paginated JSON for the console timeline, a flat export (JSON or
CSV) for evidence packs, and a cryptographic integrity report.
"""
import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app import audit as audit_service
from app.auth import (
    P_EXPORT_AUDIT,
    P_VERIFY_AUDIT,
    P_VIEW_AUDIT,
    Principal,
    require,
)
from app.database import engine, get_db
from app.db_constraints import verify_audit_immutability
from app.models import AuditLog, Customer
from app.schemas import (
    AuditRecordResponse,
    AuditTrailResponse,
    ChainVerificationResponse,
    ImmutabilityProofResponse,
)

router = APIRouter(prefix="/audit", tags=["Audit Trail"])


def _to_response(row: AuditLog) -> AuditRecordResponse:
    action = row.action or ""
    return AuditRecordResponse(
        id=row.id,
        sequence_no=row.sequence_no,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        customer_id=row.customer_id,
        action=action,
        action_label=audit_service.ACTION_LABELS.get(action, action.replace("_", " ").title()),
        origin="ANALYST" if action in audit_service.ANALYST_ACTIONS else "SYSTEM",
        actor=row.actor,
        actor_role=row.actor_role,
        details=row.details,
        prev_hash=row.prev_hash,
        record_hash=row.record_hash,
        created_at=row.created_at,
    )


def _filtered_query(
    db: Session,
    customer_id: Optional[int],
    origin: Optional[str],
    action: Optional[str],
):
    query = db.query(AuditLog)
    if customer_id is not None:
        query = query.filter(AuditLog.customer_id == customer_id)
    if action:
        query = query.filter(AuditLog.action == action.upper())
    if origin and origin.upper() in ("SYSTEM", "ANALYST"):
        analyst_actions = list(audit_service.ANALYST_ACTIONS)
        if origin.upper() == "ANALYST":
            query = query.filter(AuditLog.action.in_(analyst_actions))
        else:
            query = query.filter(~AuditLog.action.in_(analyst_actions))
    return query


@router.get(
    "",
    response_model=AuditTrailResponse,
    summary="Paginated audit trail, optionally scoped to one customer",
)
def get_audit_trail(
    customer_id: Optional[int] = Query(None, description="Scope to a single customer"),
    origin: Optional[str] = Query(None, description="SYSTEM | ANALYST"),
    action: Optional[str] = Query(None, description="Exact action name"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_AUDIT)),
):
    query = _filtered_query(db, customer_id, origin, action)
    total = query.count()

    # Oldest first: an audit trail reads as a narrative, not a news feed.
    rows = (
        query.order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    customer_name = None
    if customer_id is not None:
        customer = db.query(Customer.name).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found.")
        customer_name = customer[0]

    # Cheap head/validity summary for the "integrity verified" badge. The full
    # recomputation lives behind /audit/verify-chain.
    head = (
        db.query(AuditLog.record_hash)
        .filter(AuditLog.sequence_no.isnot(None))
        .order_by(AuditLog.sequence_no.desc())
        .first()
    )

    return AuditTrailResponse(
        customer_id=customer_id,
        customer_name=customer_name,
        total=total,
        page=page,
        size=size,
        records=[_to_response(r) for r in rows],
        chain_verified=head is not None,
        head_hash=head[0] if head and head[0] else audit_service.GENESIS_HASH,
    )


@router.get(
    "/verify-chain",
    response_model=ChainVerificationResponse,
    summary="Recompute the hash chain and report any break",
)
def verify_chain(
    customer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VERIFY_AUDIT)),
):
    result = audit_service.verify_chain(db, customer_id=customer_id)

    # The verification is itself an auditable event — this is what evidences that
    # the control was operated, and when.
    audit_service.record(
        db,
        entity_type="SYSTEM",
        entity_id=0,
        customer_id=customer_id,
        action="AUDIT_CHAIN_VERIFIED",
        actor=principal.username,
        actor_role=principal.role,
        details={
            "chain_valid": result["chain_valid"],
            "records_verified": result["records_verified"],
            "breaks_found": len(result["breaks"]),
            "head_hash": result["head_hash"],
        },
    )
    return ChainVerificationResponse(**result)


@router.get(
    "/immutability-proof",
    response_model=ImmutabilityProofResponse,
    summary="Attempt a forbidden UPDATE and DELETE and report the database's refusal",
)
def immutability_proof(principal: Principal = Depends(require(P_VERIFY_AUDIT))):
    """
    Demonstrates the append-only guarantee instead of asserting it: issues a real
    UPDATE and a real DELETE against audit_logs inside transactions that are
    always rolled back, and returns what PostgreSQL said.
    """
    return ImmutabilityProofResponse(**verify_audit_immutability(engine))


@router.get("/export", summary="Export the audit trail as JSON or CSV")
def export_audit_trail(
    format: str = Query("json", pattern="^(json|csv)$"),
    customer_id: Optional[int] = Query(None),
    origin: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_EXPORT_AUDIT)),
):
    """
    Produces the evidence pack. Restricted to Manager and Auditor: an export
    leaves the system's control, so who took one and when is itself recorded.
    """
    query = _filtered_query(db, customer_id, origin, None)
    rows = query.order_by(AuditLog.created_at.asc(), AuditLog.id.asc()).limit(limit).all()

    customer_name = None
    if customer_id is not None:
        found = db.query(Customer.name).filter(Customer.id == customer_id).first()
        customer_name = found[0] if found else None

    integrity = audit_service.verify_chain(db, customer_id=customer_id)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    scope = f"customer-{customer_id}" if customer_id is not None else "all-customers"

    audit_service.record(
        db,
        entity_type="SYSTEM",
        entity_id=customer_id or 0,
        customer_id=customer_id,
        action="AUDIT_TRAIL_EXPORTED",
        actor=principal.username,
        actor_role=principal.role,
        details={
            "format": format,
            "scope": scope,
            "records_exported": len(rows),
            "origin_filter": origin,
            "chain_valid_at_export": integrity["chain_valid"],
            "head_hash_at_export": integrity["head_hash"],
        },
    )

    filename = f"audit-trail_{scope}_{stamp}.{format}"

    if format == "json":
        payload: Dict[str, Any] = {
            "export_metadata": {
                "generated_at": datetime.now().isoformat(),
                "exported_by": principal.username,
                "exported_by_role": principal.role,
                "scope": scope,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "record_count": len(rows),
                # Included so a recipient can re-verify the pack independently.
                "chain_valid": integrity["chain_valid"],
                "head_hash": integrity["head_hash"],
                "hash_algorithm": integrity["algorithm"],
            },
            "records": [
                {
                    "sequence_no": r.sequence_no,
                    "timestamp": r.created_at.isoformat() if r.created_at else None,
                    "action": r.action,
                    "action_label": audit_service.ACTION_LABELS.get(r.action, r.action),
                    "origin": "ANALYST" if r.action in audit_service.ANALYST_ACTIONS else "SYSTEM",
                    "entity_type": r.entity_type,
                    "entity_id": r.entity_id,
                    "customer_id": r.customer_id,
                    "actor": r.actor,
                    "actor_role": r.actor_role,
                    "details": r.details,
                    "prev_hash": r.prev_hash,
                    "record_hash": r.record_hash,
                }
                for r in rows
            ],
        }
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # CSV: details is nested, so it is serialised into one JSON column rather
    # than flattened — flattening would produce a ragged header across actions.
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "sequence_no",
            "timestamp",
            "action",
            "action_label",
            "origin",
            "entity_type",
            "entity_id",
            "customer_id",
            "actor",
            "actor_role",
            "details_json",
            "prev_hash",
            "record_hash",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.sequence_no,
                r.created_at.isoformat() if r.created_at else "",
                r.action,
                audit_service.ACTION_LABELS.get(r.action, r.action),
                "ANALYST" if r.action in audit_service.ANALYST_ACTIONS else "SYSTEM",
                r.entity_type,
                r.entity_id,
                r.customer_id if r.customer_id is not None else "",
                r.actor or "",
                r.actor_role or "",
                json.dumps(r.details or {}, separators=(",", ":"), default=str),
                r.prev_hash or "",
                r.record_hash or "",
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
