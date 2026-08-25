"""Evaluation harness endpoints — the baseline-vs-engine comparison report."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import P_RUN_EVALUATION, P_VIEW_EVALUATION, Principal, require
from app.database import get_db
from app.evaluation import ScenarioConfig, run_evaluation
from app.models import EvaluationRun
from app.schemas import EvaluationRunRequest, EvaluationRunResponse

router = APIRouter(prefix="/evaluation", tags=["Evaluation Harness"])


@router.post(
    "/run",
    response_model=EvaluationRunResponse,
    summary="Generate a labeled scenario and score baseline against the engine",
)
def trigger_evaluation(
    req: Optional[EvaluationRunRequest] = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_RUN_EVALUATION)),
):
    """
    Runs synchronously — the default 5,000-event scenario completes in well under
    a minute, and a synchronous call keeps the demo honest about how long the
    measurement actually takes.
    """
    req = req or EvaluationRunRequest()
    config = ScenarioConfig(
        num_customers=req.num_customers,
        num_events=req.num_events,
        horizon_days=req.horizon_days,
        baseline_review_interval_days=req.baseline_review_interval_days,
        random_seed=req.random_seed,
        fuzzy_match_threshold=req.fuzzy_match_threshold,
    )
    record = run_evaluation(db, config, actor=principal.username, actor_role=principal.role)
    return EvaluationRunResponse.model_validate(record)


@router.get(
    "/latest",
    response_model=EvaluationRunResponse,
    summary="Most recent evaluation report",
)
def latest_evaluation(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_EVALUATION)),
):
    record = db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).first()
    if not record:
        raise HTTPException(
            status_code=404,
            detail="No evaluation has been run yet. POST /evaluation/run as a Manager.",
        )
    return EvaluationRunResponse.model_validate(record)


@router.get(
    "/runs",
    response_model=List[EvaluationRunResponse],
    summary="History of evaluation runs",
)
def list_evaluation_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_EVALUATION)),
):
    records = (
        db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit).all()
    )
    return [EvaluationRunResponse.model_validate(r) for r in records]


@router.get(
    "/{run_id}",
    response_model=EvaluationRunResponse,
    summary="A specific evaluation report",
)
def get_evaluation_run(
    run_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require(P_VIEW_EVALUATION)),
):
    record = db.query(EvaluationRun).filter(EvaluationRun.id == run_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Evaluation run {run_id} not found.")
    return EvaluationRunResponse.model_validate(record)
