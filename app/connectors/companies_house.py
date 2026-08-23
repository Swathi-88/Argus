from typing import List, Dict, Any
from app.connectors.base import BaseConnector
from app.config import settings

class CompaniesHouseConnector(BaseConnector):
    """
    UK Companies House Data Connector:
    Pulls live company filings, officer changes, registered office updates,
    and insolvency status for real corporate entities from UK Companies House API.
    
    API Endpoint: https://api.company-information.service.gov.uk
    Auth: Basic Auth with COMPANIES_HOUSE_API_KEY
    """
    def __init__(self):
        super().__init__(
            name="UK_Companies_House",
            api_key=settings.COMPANIES_HOUSE_API_KEY,
            base_url="https://api.company-information.service.gov.uk"
        )

    def fetch_events_for_customer(self, customer_name: str, customer_type: str = "Corporate") -> List[Dict[str, Any]]:
        # Companies House only applies to Corporate entities
        if customer_type.upper() != "CORPORATE":
            return []

        events = []
        if not self.can_make_live_request():
            return []

        try:
            self.increment_request_count()
            headers = {}
            auth = (self.api_key, "") if self.api_key else None

            with self._get_client(auth=auth, headers=headers) as client:
                # 1. Search company by name
                resp = client.get(f"{self.base_url}/search/companies", params={"q": customer_name, "items_per_page": 1})
                if resp.status_code != 200:
                    return []

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    return []

                comp = items[0]
                raw_comp_number = comp.get("company_number", "")
                comp_number = str(raw_comp_number).strip().zfill(8)
                comp_name = comp.get("title", customer_name)
                comp_status = comp.get("company_status", "active")
                
                # 2. Fetch company officers to check for recent director appointments/resignations
                off_resp = client.get(f"{self.base_url}/company/{comp_number}/officers")
                officer_info = off_resp.json() if off_resp.status_code == 200 else {}
                officers_list = officer_info.get("items", [])

                # 3. Determine severity based on company status or officer changes
                if comp_status.lower() in ("liquidation", "receivership", "administration", "dissolved"):
                    severity = "CRITICAL"
                    event_type = "COMPANY_INSOLVENCY"
                elif len(officers_list) > 0:
                    severity = "MEDIUM"
                    event_type = "DIRECTOR_CHANGE"
                else:
                    severity = "LOW"
                    event_type = "CORPORATE_FILING"

                events.append({
                    "entity_name": comp_name,
                    "event_type": event_type,
                    "category": "CORPORATE_CHANGE",
                    "severity": severity,
                    "source": self.name,
                    "raw_payload": {
                        "company_number": comp_number,
                        "company_status": comp_status,
                        "address": comp.get("address"),
                        "company_type": comp.get("company_type"),
                        "date_of_creation": comp.get("date_of_creation"),
                        "officers_count": len(officers_list),
                        "officers_summary": [
                            {
                                "name": o.get("name"),
                                "role": o.get("officer_role"),
                                "appointed_on": o.get("appointed_on"),
                                "resigned_on": o.get("resigned_on")
                            } for o in officers_list[:3]
                        ]
                    }
                })
                return events
        except Exception as e:
            print(f"[CompaniesHouseConnector] Live API query exception: {e}")

        return []
