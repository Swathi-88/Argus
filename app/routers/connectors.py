from typing import Optional
from fastapi import APIRouter, Body, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.connectors.manager import ConnectorManager
from app.worker import WorkerProcess
from app.schemas import ConnectorRunResult, ConnectorRunRequest

router = APIRouter(prefix="/connectors", tags=["Data Connectors"])
connector_manager = ConnectorManager()
worker = WorkerProcess()

@router.post("/run", response_model=ConnectorRunResult)
def run_connector(
    request: Optional[ConnectorRunRequest] = Body(default=None),

    process_immediately: bool = Query(True, description="Whether worker should process queued events immediately"),
    db: Session = Depends(get_db)
):
    """
    Triggers external API data connectors (UK Companies House, OpenSanctions, FCA Register, NewsAPI),
    fetches external risk signals matching synthetic customer records, pushes raw events into Redis Stream,
    and runs worker processing pipeline.
    """
    if request is None:
        request = ConnectorRunRequest()

    result = connector_manager.fetch_and_enqueue_all(
        db=db,
        limit_customers=request.limit_customers,
        specific_connector=request.connector if request.connector != "all" else None
    )
    
    events_materialized = 0
    if process_immediately and result["total_events_queued"] > 0:
        # Process queued events using WorkerProcess
        processed = worker.process_batch(batch_size=result["total_events_queued"] + 5)
        events_materialized = sum(1 for m in processed if m is not None)

    return ConnectorRunResult(
        connector=request.connector,
        events_pulled=result["total_events_pulled"],
        events_queued=result["total_events_queued"],
        events_materialized=events_materialized,
        details=result["details"]
    )
