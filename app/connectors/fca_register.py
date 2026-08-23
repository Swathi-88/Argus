from typing import List, Dict, Any
from app.connectors.base import BaseConnector
from app.config import settings

class FCARegisterConnector(BaseConnector):
    """
    FCA Financial Services Register API Connector:
    Checks if a firm is authorized on the official UK Financial Conduct Authority (FCA) Register
    and verifies its CASS client money holding permissions.
    
    API Endpoint: https://register.fca.org.uk / FCA Firm Search API
    """
    def __init__(self):
        super().__init__(
            name="FCA_Register",
            api_key=settings.FCA_API_KEY,
            base_url="https://register.fca.org.uk/services/V0.1"
        )

    def fetch_events_for_customer(self, customer_name: str, customer_type: str = "Corporate") -> List[Dict[str, Any]]:
        if customer_type.upper() != "CORPORATE":
            return []

        events = []
        if not self.can_make_live_request():
            return []

        try:
            self.increment_request_count()

            headers = {
                "X-Auth-Email": (settings.FCA_AUTH_EMAIL or "").strip(),
                "X-Auth-Key": (self.api_key or "").strip(),
                "Content-Type": "application/json"
            }

            with self._get_client(headers=headers) as client:
                # 1. Search firm by FRN or Name
                if customer_name.isdigit():
                    firm_resp = client.get(f"{self.base_url}/Firm/{customer_name}")
                    firms = [firm_resp.json()] if firm_resp.status_code == 200 else []
                else:
                    resp = client.get(f"{self.base_url}/Search", params={"q": customer_name})
                    firms = resp.json().get("Data", []) if resp.status_code == 200 else []

                if not firms:
                    return []

                firm = firms[0]
                frn = str(firm.get("FRN", "")).strip()
                
                # 2. Check firm permissions via FCA Permissions API
                perm_resp = client.get(f"{self.base_url}/Firm/{frn}/Permissions")
                perms = perm_resp.json() if perm_resp.status_code == 200 else {}
                    
                client_money_status = perms.get("ClientMoney", {}).get("HoldsClientMoney", False)
                firm_status = firm.get("Status", "Active")
                
                is_revoked = (not client_money_status) or ("SUSPENDED" in firm_status.upper()) or ("RESTRICTED" in firm_status.upper())

                events.append({
                    "entity_name": firm.get("Name", customer_name),
                    "event_type": "CLIENT_MONEY_REVOCATION" if is_revoked else "FCA_AUTHORIZATION_CONFIRMED",
                    "category": "REGULATORY_CHANGE",
                    "severity": "CRITICAL" if is_revoked else "LOW",
                    "source": self.name,
                    "raw_payload": {
                        "frn": frn,
                        "firm_status": firm_status,
                        "holds_client_money": client_money_status,
                        "client_money_check": "REVOKED / UNAUTHORIZED" if is_revoked else "PASSED - Authorized to hold client money",
                        "regulator": "UK Financial Conduct Authority (FCA)"
                    }
                })
                return events
        except Exception as e:
            print(f"[FCARegisterConnector] Live FCA API query exception: {e}")

        return []
