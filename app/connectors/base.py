from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)

class BaseConnector(ABC):
    """
    Abstract Base Class for External Data Connectors.
    Provides standard HTTP client methods, timeout/retry handling,
    strict rate-limiting safeguards, and mock/fallback response generation.
    """
    def __init__(self, name: str, api_key: Optional[str] = None, base_url: str = ""):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.request_count = 0
        self.max_requests = settings.MAX_LIVE_API_REQUESTS_PER_RUN

    def _get_client(self, headers: Optional[Dict[str, str]] = None, auth: Optional[Any] = None) -> httpx.Client:
        return httpx.Client(timeout=10.0, headers=headers or {}, auth=auth, follow_redirects=True)

    def can_make_live_request(self) -> bool:
        """
        Safeguard check: verifies if live API calls are explicitly enabled, API key exists,
        and request count has not exceeded maximum allowed limit per run (e.g. max 2-3 requests/day).
        """
        if not settings.ENABLE_LIVE_API_CALLS:
            return False
        if not self.api_key or not self.api_key.strip():
            return False
        if self.request_count >= self.max_requests:
            print(f"[{self.name}] Rate limit safeguard reached ({self.request_count}/{self.max_requests} live calls). Switching to mock fallback.")
            return False
        return True

    def increment_request_count(self):
        self.request_count += 1

    @abstractmethod
    def fetch_events_for_customer(self, customer_name: str, customer_type: str = "Corporate") -> List[Dict[str, Any]]:
        """
        Fetch external events matching customer entity.
        Returns a list of raw event payload dicts ready for ingestion queue.
        """
        pass

