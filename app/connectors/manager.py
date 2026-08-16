from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models import Customer
from app.connectors.companies_house import CompaniesHouseConnector
from app.connectors.opensanctions import OpenSanctionsConnector
from app.connectors.fca_register import FCARegisterConnector
from app.connectors.news_api import NewsAPIConnector
from app.queue import event_queue

class ConnectorManager:
    """
    ConnectorManager:
    Orchestrates external data connectors (Companies House, OpenSanctions, FCA Register, NewsAPI),
    fetches external risk signals matching synthetic customer records, and pushes raw events
    into the Redis Streams Event Queue.
    """
    def __init__(self):
        self.connectors = {
            "companies_house": CompaniesHouseConnector(),
            "opensanctions": OpenSanctionsConnector(),
            "fca_register": FCARegisterConnector(),
            "news_api": NewsAPIConnector()
        }

    def fetch_and_enqueue_all(
        self,
        db: Session,
        limit_customers: int = 10,
        specific_connector: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Queries target customers from DB, runs external connectors, and publishes
        raw event payloads to the Redis Stream event queue.
        """
        customers = db.query(Customer).order_by(Customer.id.asc()).limit(limit_customers).all()
        
        target_connectors = {}
        if specific_connector and specific_connector.lower() in self.connectors:
            target_connectors[specific_connector.lower()] = self.connectors[specific_connector.lower()]
        else:
            target_connectors = self.connectors

        total_pulled = 0
        total_queued = 0
        details_by_connector = {}

        for conn_name, connector in target_connectors.items():
            conn_pulled = 0
            conn_queued = 0
            
            for cust in customers:
                raw_events = connector.fetch_events_for_customer(
                    customer_name=cust.name,
                    customer_type=cust.type
                )
                conn_pulled += len(raw_events)
                
                for ev in raw_events:
                    # Push raw event payload into Redis Streams event queue
                    event_id = event_queue.publish(
                        entity_name=ev["entity_name"],
                        event_type=ev["event_type"],
                        category=ev.get("category"),
                        severity=ev.get("severity"),
                        source=ev["source"],
                        raw_payload=ev.get("raw_payload", {})
                    )
                    if event_id:
                        conn_queued += 1
            
            total_pulled += conn_pulled
            total_queued += conn_queued
            details_by_connector[conn_name] = {
                "events_pulled": conn_pulled,
                "events_queued": conn_queued
            }

        return {
            "connector": specific_connector or "all",
            "customers_scanned": len(customers),
            "total_events_pulled": total_pulled,
            "total_events_queued": total_queued,
            "details": details_by_connector
        }
