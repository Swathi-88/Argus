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


def build_target_index(db: Session) -> List[Dict]:
    """
    Materialises every match target — customer names plus registered aliases —
    with its normalized form precomputed.

    Split out from `resolve` so a caller processing many events can build the
    index once. Previously every single resolution reloaded the entire customer
    table, which made bulk evaluation quadratic.
    """
    targets: List[Dict] = []

    for cust_id, cust_name in db.query(Customer.id, Customer.name).all():
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

    return targets


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
        return self.resolve_against(query_name, build_target_index(db))

    def score_against(self, query_name: str, targets: List[Dict]) -> Dict:
        """
        Runs the three matching tiers and returns the best candidate found,
        **without** applying the fuzzy threshold.

        Separating scoring from the accept/reject decision means a caller can
        re-decide at a different threshold without re-scoring — which is what
        makes the evaluation harness's threshold sensitivity sweep cheap instead
        of an N-times-slower rerun.

        Returns a dict with `tier` (EXACT | NORMALIZED | FUZZY | NONE), the best
        target, and the raw combined similarity for the fuzzy tier.
        """
        if not query_name or not query_name.strip():
            return {"tier": "NONE", "target": None, "score": 0.0}

        raw_query = query_name.strip()
        lower_query = raw_query.lower()
        norm_query = normalize_name(raw_query)

        # --- Tier 1: Exact Match (Case-Insensitive) ---
        for target in targets:
            if lower_query == target["lower_string"]:
                return {"tier": "EXACT", "target": target, "score": 100.0}

        # --- Tier 2: Normalized Match ---
        if norm_query:
            for target in targets:
                if target["norm_string"] and norm_query == target["norm_string"]:
                    return {"tier": "NORMALIZED", "target": target, "score": 95.0}

        # --- Tier 3: Fuzzy Match (Jaro-Winkler + Token Similarity) ---
        best_target = None
        best_score = 0.0

        if norm_query:
            for target in targets:
                target_norm = target["norm_string"] or target["lower_string"]
                if not target_norm:
                    continue

                # Jaro-Winkler similarity (0 to 100)
                jw_sim = JaroWinkler.similarity(norm_query, target_norm) * 100.0

                # Token Sort Ratio similarity (0 to 100)
                token_sim = fuzz.token_sort_ratio(norm_query, target_norm)

                # Combined metric: weighted average
                combined_sim = 0.5 * jw_sim + 0.5 * token_sim

                if combined_sim > best_score:
                    best_score = combined_sim
                    best_target = target

        if best_target:
            return {"tier": "FUZZY", "target": best_target, "score": best_score}

        return {"tier": "NONE", "target": None, "score": 0.0}

    @staticmethod
    def decide(candidate: Dict, fuzzy_threshold: float) -> EntityResolutionResult:
        """Turns a `score_against` candidate into a result at the given threshold."""
        tier = candidate["tier"]
        target = candidate["target"]

        if tier == "EXACT":
            return EntityResolutionResult(
                matched_customer_id=target["customer_id"],
                matched_customer_name=target["customer_name"],
                matched_string=target["target_string"],
                confidence_score=100.0,
                match_method="EXACT",
            )

        if tier == "NORMALIZED":
            return EntityResolutionResult(
                matched_customer_id=target["customer_id"],
                matched_customer_name=target["customer_name"],
                matched_string=target["target_string"],
                confidence_score=95.0,
                match_method="NORMALIZED",
            )

        if tier == "FUZZY" and candidate["score"] >= fuzzy_threshold:
            # Capped at 90.0 so a fuzzy hit never claims exact/normalized confidence.
            return EntityResolutionResult(
                matched_customer_id=target["customer_id"],
                matched_customer_name=target["customer_name"],
                matched_string=target["target_string"],
                confidence_score=min(90.0, round(candidate["score"], 2)),
                match_method="FUZZY",
            )

        # --- Tier 4: No Match ---
        return EntityResolutionResult(
            matched_customer_id=None,
            matched_customer_name=None,
            matched_string=None,
            confidence_score=round(candidate["score"], 2) if target else 0.0,
            match_method="UNMATCHED",
        )

    def resolve_against(self, query_name: str, targets: List[Dict]) -> EntityResolutionResult:
        """
        The matching logic itself, against a prebuilt target index.

        `resolve` is a thin wrapper over this, so a caller holding an index gets
        byte-identical behaviour — the evaluation harness depends on this being
        the same code path the live pipeline runs.
        """
        return self.decide(self.score_against(query_name, targets), self.fuzzy_threshold)
