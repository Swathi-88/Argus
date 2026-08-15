import re
from typing import List, Tuple, Optional, Dict
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler
from sqlalchemy.orm import Session

from app.models import Customer, EntityAlias
from app.schemas import EntityResolutionResult

# Suffixes to strip during normalization
LEGAL_SUFFIXES = re.compile(
    r'\b(ltd|limited|inc|incorporated|corp|corporation|llc|gmbh|plc|co|company|pty|sa|group|holdings|services|international|int)\b',
    re.IGNORECASE
)

def normalize_name(name: str) -> str:
    """
    Normalizes an entity name by lowercase, stripping punctuation,
    removing common legal suffixes, and trimming extra spaces.
    """
    if not name:
        return ""
    
    cleaned = name.lower()
    # Strip punctuation without extra spaces so S.A. -> sa, Ltd. -> ltd
    cleaned = re.sub(r'[^\w\s]', '', cleaned)
    # Remove legal suffixes
    cleaned = LEGAL_SUFFIXES.sub("", cleaned)
    # Collapse multiple whitespaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


class EntityResolver:
    def __init__(self, fuzzy_threshold: float = 65.0):
        self.fuzzy_threshold = fuzzy_threshold

    def resolve(self, query_name: str, db: Session) -> EntityResolutionResult:
        """
        Multi-tier Entity Resolution against Customers and EntityAliases:
        1. Exact Match -> Confidence 100.0
        2. Normalized Match -> Confidence 95.0
        3. Fuzzy Match (Jaro-Winkler + Token similarity) -> Confidence 0 - 90.0
        """
        if not query_name or not query_name.strip():
            return EntityResolutionResult(
                matched_customer_id=None,
                matched_customer_name=None,
                matched_string=None,
                confidence_score=0.0,
                match_method="UNMATCHED"
            )

        raw_query = query_name.strip()
        lower_query = raw_query.lower()
        norm_query = normalize_name(raw_query)

        # Retrieve target customer & alias records
        # Tuples: (customer_id, customer_name, entity_string, source_type)
        targets: List[Dict] = []
        
        customers = db.query(Customer.id, Customer.name).all()
        for cust_id, cust_name in customers:
            targets.append({
                "customer_id": cust_id,
                "customer_name": cust_name,
                "target_string": cust_name,
                "lower_string": cust_name.strip().lower(),
                "norm_string": normalize_name(cust_name)
            })
            
        aliases = db.query(EntityAlias.customer_id, EntityAlias.alias_name, Customer.name)\
                    .join(Customer, Customer.id == EntityAlias.customer_id).all()
        for cust_id, alias_name, cust_name in aliases:
            targets.append({
                "customer_id": cust_id,
                "customer_name": cust_name,
                "target_string": alias_name,
                "lower_string": alias_name.strip().lower(),
                "norm_string": normalize_name(alias_name)
            })

        # --- Tier 1: Exact Match (Case-Insensitive) ---
        for target in targets:
            if lower_query == target["lower_string"]:
                return EntityResolutionResult(
                    matched_customer_id=target["customer_id"],
                    matched_customer_name=target["customer_name"],
                    matched_string=target["target_string"],
                    confidence_score=100.0,
                    match_method="EXACT"
                )

        # --- Tier 2: Normalized Match ---
        if norm_query:
            for target in targets:
                if target["norm_string"] and norm_query == target["norm_string"]:
                    return EntityResolutionResult(
                        matched_customer_id=target["customer_id"],
                        matched_customer_name=target["customer_name"],
                        matched_string=target["target_string"],
                        confidence_score=95.0,
                        match_method="NORMALIZED"
                    )

        # --- Tier 3: Fuzzy Match (Jaro-Winkler + Token Similarity) ---
        best_target = None
        best_score = 0.0

        if norm_query:
            for target in targets:
                target_norm = target["norm_string"] or target["lower_string"]
                if not target_norm:
                    continue

                # Calculate Jaro-Winkler similarity (0 to 100)
                jw_sim = JaroWinkler.similarity(norm_query, target_norm) * 100.0
                
                # Calculate Token Sort Ratio similarity (0 to 100)
                token_sim = fuzz.token_sort_ratio(norm_query, target_norm)
                
                # Combined metric: weighted average
                combined_sim = 0.5 * jw_sim + 0.5 * token_sim

                if combined_sim > best_score:
                    best_score = combined_sim
                    best_target = target

        if best_target and best_score >= self.fuzzy_threshold:
            # Map fuzzy score to a max confidence of 90.0 to distinguish from exact/normalized
            fuzzy_confidence = min(90.0, round(best_score, 2))
            return EntityResolutionResult(
                matched_customer_id=best_target["customer_id"],
                matched_customer_name=best_target["customer_name"],
                matched_string=best_target["target_string"],
                confidence_score=fuzzy_confidence,
                match_method="FUZZY"
            )

        # --- Tier 4: No Match ---
        return EntityResolutionResult(
            matched_customer_id=None,
            matched_customer_name=None,
            matched_string=None,
            confidence_score=round(best_score, 2) if best_target else 0.0,
            match_method="UNMATCHED"
        )
