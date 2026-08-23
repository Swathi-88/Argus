from typing import List, Dict, Any
from app.connectors.base import BaseConnector
from app.config import settings

ADVERSE_KEYWORDS = ["fraud", "investigation", "laundering", "bribery", "scandal", "lawsuit", "indicted", "corruption"]

class NewsAPIConnector(BaseConnector):
    """
    NewsAPI Connector (Adverse Media Signals):
    Pulls recent news headlines mentioning real customer names combined with adverse financial keywords
    for negative news screening.
    
    API Endpoint: https://newsapi.org/v2/everything
    """
    def __init__(self):
        super().__init__(
            name="NewsAPI_AdverseMedia",
            api_key=settings.NEWS_API_KEY,
            base_url="https://newsapi.org/v2/everything"
        )

    def fetch_events_for_customer(self, customer_name: str, customer_type: str = "Corporate") -> List[Dict[str, Any]]:
        events = []
        if not self.can_make_live_request():
            return []

        try:
            self.increment_request_count()

            query = f'"{customer_name}" AND (fraud OR investigation OR laundering OR scandal OR lawsuit OR corruption)'
            params = {
                "q": query,
                "sortBy": "publishedAt",
                "pageSize": 2,
                "apiKey": self.api_key
            }
            with self._get_client() as client:
                resp = client.get(self.base_url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    articles = data.get("articles", [])
                    for article in articles:
                        title = article.get("title", "")
                        desc = article.get("description", "")
                        combined = f"{title} {desc}".lower()

                        # Evaluate severity based on adverse keywords in live news
                        if any(k in combined for k in ["fraud", "laundering", "bribery", "indicted"]):
                            severity = "HIGH"
                            event_type = "FRAUD_ALLEGATION"
                        else:
                            severity = "MEDIUM"
                            event_type = "NEGATIVE_NEWS"

                        events.append({
                            "entity_name": customer_name,
                            "event_type": event_type,
                            "category": "ADVERSE_MEDIA",
                            "severity": severity,
                            "source": f"{self.name} - {article.get('source', {}).get('name', 'Media')}",
                            "raw_payload": {
                                "headline": title,
                                "description": desc,
                                "url": article.get("url"),
                                "published_at": article.get("publishedAt"),
                                "author": article.get("author")
                            }
                        })
                    return events
        except Exception as e:
            print(f"[NewsAPIConnector] Live NewsAPI query exception: {e}")

        return []
