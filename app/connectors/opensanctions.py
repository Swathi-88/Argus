from typing import List, Dict, Any
from app.connectors.base import BaseConnector
from app.config import settings

class OpenSanctionsConnector(BaseConnector):
    """
    OpenSanctions.org API Connector:
    Screens real customer entity names against international sanctions, PEP lists,
    and official global watchlists.
    
    API Endpoint: https://api.opensanctions.org/search/default
    """
    def __init__(self):
        super().__init__(
            name="OpenSanctions_API",
            api_key=settings.OPENSANCTIONS_API_KEY,
            base_url="https://api.opensanctions.org"
        )

    def fetch_events_for_customer(self, customer_name: str, customer_type: str = "Corporate") -> List[Dict[str, Any]]:
        events = []
        if not self.can_make_live_request():
            return []

        try:
            self.increment_request_count()
            headers = {"Authorization": f"ApiKey {self.api_key}"} if self.api_key else {}
            
            with self._get_client(headers=headers) as client:
                resp = client.get(f"{self.base_url}/search/default", params={"q": customer_name, "limit": 2})
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    for match in results:
                        score = float(match.get("score", 0.0))
                        # Only accept high-confidence matches (score >= 0.70)
                        if score >= 0.70:
                            topics = match.get("topics", [])
                            is_pep = "role.pep" in topics
                            is_sanction = "sanction" in topics
                            
                            event_type = "SANCTIONS_UPDATE" if is_sanction else ("PEP_LISTING" if is_pep else "WATCHLIST_MATCH")
                            severity = "CRITICAL" if is_sanction else ("HIGH" if is_pep else "MEDIUM")

                            events.append({
                                "entity_name": match.get("caption", customer_name),
                                "event_type": event_type,
                                "category": "SANCTIONS_MATCH",
                                "severity": severity,
                                "source": self.name,
                                "raw_payload": {
                                    "entity_id": match.get("id"),
                                    "schema": match.get("schema"),
                                    "countries": match.get("countries", []),
                                    "datasets": match.get("datasets", []),
                                    "topics": topics,
                                    "target_score": score,
                                    "first_seen": match.get("first_seen"),
                                    "last_seen": match.get("last_seen")
                                }
                            })
                    return events
        except Exception as e:
            print(f"[OpenSanctionsConnector] Live OpenSanctions API exception: {e}")

        return []
