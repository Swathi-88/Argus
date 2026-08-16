import os
import json
from typing import Dict, Any, Tuple, Optional

RULES_FILE = os.path.join(os.path.dirname(__file__), "classifier_rules.json")

class SignalClassifier:
    """
    Signal Classifier Module:
    Tags every incoming event with:
    - category: REGULATORY_CHANGE | SANCTIONS_MATCH | ADVERSE_MEDIA | TRANSACTION_ANOMALY | CORPORATE_CHANGE
    - severity: LOW | MEDIUM | HIGH | CRITICAL
    
    Uses a configurable rule table loaded from a JSON configuration file.
    """
    def __init__(self, rules_filepath: str = RULES_FILE):
        self.rules_filepath = rules_filepath
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, Any]:
        if os.path.exists(self.rules_filepath):
            try:
                with open(self.rules_filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Classifier] Warning: Could not parse rules file ({e}). Using default rules.")
        
        # Fallback default rules table
        return {
            "categories": {
                "SANCTIONS_MATCH": {"default_severity": "CRITICAL", "weight": 4.0, "keywords": ["sanction", "pep", "ofac", "un_list"]},
                "REGULATORY_CHANGE": {"default_severity": "HIGH", "weight": 3.0, "keywords": ["fca", "client_money", "cass", "unauthorized"]},
                "ADVERSE_MEDIA": {"default_severity": "MEDIUM", "weight": 2.0, "keywords": ["fraud", "adverse_news", "investigation", "scandal"]},
                "TRANSACTION_ANOMALY": {"default_severity": "HIGH", "weight": 3.0, "keywords": ["transaction_spike", "offshore", "structuring"]},
                "CORPORATE_CHANGE": {"default_severity": "LOW", "weight": 1.0, "keywords": ["director", "filing", "registered_office", "companies_house"]}
            },
            "severity_weights": {
                "LOW": 1.0, "MEDIUM": 2.0, "HIGH": 3.0, "CRITICAL": 4.0
            },
            "event_type_mappings": {
                "SANCTIONS_UPDATE": {"category": "SANCTIONS_MATCH", "severity": "CRITICAL"},
                "PEP_LISTING": {"category": "SANCTIONS_MATCH", "severity": "HIGH"},
                "CLIENT_MONEY_REVOCATION": {"category": "REGULATORY_CHANGE", "severity": "CRITICAL"},
                "FCA_AUTHORIZATION_CHANGE": {"category": "REGULATORY_CHANGE", "severity": "HIGH"},
                "NEGATIVE_NEWS": {"category": "ADVERSE_MEDIA", "severity": "MEDIUM"},
                "FRAUD_ALLEGATION": {"category": "ADVERSE_MEDIA", "severity": "HIGH"},
                "TRANSACTION_SPIKE": {"category": "TRANSACTION_ANOMALY", "severity": "HIGH"},
                "DIRECTOR_CHANGE": {"category": "CORPORATE_CHANGE", "severity": "LOW"},
                "FILING_OVERDUE": {"category": "CORPORATE_CHANGE", "severity": "MEDIUM"}
            }
        }

    def get_severity_weight(self, category: str, severity: str) -> float:
        cat_upper = category.upper() if category else "CORPORATE_CHANGE"
        sev_upper = severity.upper() if severity else "LOW"
        
        pair_weights = self.rules.get("category_severity_weights", {})
        if cat_upper in pair_weights and sev_upper in pair_weights[cat_upper]:
            return float(pair_weights[cat_upper][sev_upper])
        
        # Fallback to flat severity weights or 1.0 default
        flat_weights = self.rules.get("severity_weights", {"LOW": 1.2, "MEDIUM": 4.0, "HIGH": 10.0, "CRITICAL": 35.0})
        return float(flat_weights.get(sev_upper, 1.0))

    def classify(
        self,
        event_type: str,
        raw_payload: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        requested_category: Optional[str] = None,
        requested_severity: Optional[str] = None
    ) -> Tuple[str, str, float]:
        """
        Classifies an event and returns (category, severity, category_severity_weight).
        
        Priority:
        1. Explicit valid category & severity passed in request.
        2. Exact event_type mapping in rule table.
        3. Keyword matching against event_type, source, and payload text.
        4. Default fallback: CORPORATE_CHANGE, LOW.
        """
        raw_payload = raw_payload or {}
        event_type_upper = (event_type or "").upper().strip()
        source_upper = (source or "").upper().strip()
        
        valid_categories = set(self.rules.get("categories", {}).keys())
        valid_severities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

        # 1. Direct explicit override if valid
        if requested_category and requested_category.upper() in valid_categories:
            cat = requested_category.upper()
            sev = requested_severity.upper() if requested_severity and requested_severity.upper() in valid_severities else self.rules["categories"][cat]["default_severity"]
            return cat, sev, self.get_severity_weight(cat, sev)

        # 2. Match exact event_type mapping
        mappings = self.rules.get("event_type_mappings", {})
        if event_type_upper in mappings:
            mapping = mappings[event_type_upper]
            cat = mapping["category"]
            sev = requested_severity.upper() if requested_severity and requested_severity.upper() in valid_severities else mapping["severity"]
            return cat, sev, self.get_severity_weight(cat, sev)

        # 3. Text search over payload, event_type, and source
        combined_text = f"{event_type_upper} {source_upper} {json.dumps(raw_payload).upper()}"
        
        best_cat = None
        highest_match_count = 0
        
        for cat_name, cat_info in self.rules.get("categories", {}).items():
            keywords = cat_info.get("keywords", [])
            matches = sum(1 for kw in keywords if kw.upper() in combined_text)
            if matches > highest_match_count:
                highest_match_count = matches
                best_cat = cat_name

        if best_cat:
            sev = requested_severity.upper() if requested_severity and requested_severity.upper() in valid_severities else self.rules["categories"][best_cat]["default_severity"]
            return best_cat, sev, self.get_severity_weight(best_cat, sev)

        # 4. Fallback default
        cat = "CORPORATE_CHANGE"
        sev = requested_severity.upper() if requested_severity and requested_severity.upper() in valid_severities else "LOW"
        return cat, sev, self.get_severity_weight(cat, sev)

