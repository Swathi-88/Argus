"""
Baseline vs engine evaluation.

Two policies see the identical labeled event stream:

*   **Baseline** — static periodic review. Each customer is reassessed every
    ``baseline_review_interval_days`` from their onboarding anniversary,
    regardless of what happens in between. This is the incumbent control in most
    banks and the one the FCA enforcement notices repeatedly find wanting.

*   **Engine** — event-driven continuous reassessment. Every event is resolved,
    classified, passed through the materiality gate, and scored.

## Fidelity of the engine run

The engine arm uses the **production components**, not a reimplementation:
``SignalClassifier`` for classification, ``EntityResolver.resolve_against`` for
matching (the same function the live pipeline calls, given a prebuilt index),
``MaterialityGate.evaluate`` for the gate policy, and ``BayesianRiskEngine`` for
scoring. Only two things are substituted, both of them I/O rather than policy:

1.  deduplication is answered from an in-memory index instead of a SQL query
    (via the gate's ``duplicate_lookup`` seam), and
2.  scoring runs with ``persist=False`` so 5,000 events do not write 5,000 rows
    into the live tables.

Customers and events are held as transient SQLAlchemy objects that are never
added to a session, so the components receive exactly the shapes they expect.

## What is being counted

The unit of analysis is **the event**, and the question is the one the ground
truth answers: *should this event have triggered a reassessment?*

*   TP — event is material, the system reassessed on it.
*   FN — event is material, the system did not.
*   FP — event is not material, the system reassessed on it.
*   TN — event is not material, the system did not.

For the baseline, "reassessed on it" means a scheduled review fell after the
event, inside the horizon, while the evidence was still discoverable. Note this
makes the baseline **indiscriminate rather than inaccurate**: a periodic review
sweeps whatever is in the file, so it picks up noise and signal alike. That is
why its false-positive rate is high and its distinguishing weakness is latency,
not recall — and why analyst workload is reported alongside.
"""
import logging
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import audit
from app.classifier import SignalClassifier
from app.config import settings
from app.entity_resolution import EntityResolver, normalize_name
from app.evaluation.scenario import Scenario, ScenarioConfig, generate_scenario
from app.materiality import MaterialityGate
from app.models import Customer, EvaluationRun, Event
from app.risk_engine import BayesianRiskEngine, map_probability_to_tier

logger = logging.getLogger(__name__)

# Buckets for the detection-latency histogram, in days.
LATENCY_BUCKETS: List[Tuple[str, float, float]] = [
    ("Same day", 0.0, 1.0),
    ("1–7 days", 1.0, 7.0),
    ("8–30 days", 7.0, 30.0),
    ("31–60 days", 30.0, 60.0),
    ("61–90 days", 60.0, 90.0),
    ("Over 90 days", 90.0, math.inf),
]

# Sampling points for the cumulative-detection curve, in days since the event.
CUMULATIVE_CURVE_DAYS = [0, 1, 3, 7, 14, 21, 30, 45, 60, 75, 90, 120, 150, 180]


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 4)


def _latency_stats(latencies: List[float]) -> Dict[str, Optional[float]]:
    if not latencies:
        return {"count": 0, "mean_days": None, "median_days": None, "p90_days": None, "max_days": None}
    return {
        "count": len(latencies),
        "mean_days": round(statistics.fmean(latencies), 3),
        "median_days": round(statistics.median(latencies), 3),
        "p90_days": _percentile(latencies, 0.90),
        "max_days": round(max(latencies), 3),
    }


def _classification_metrics(tp: int, fp: int, fn: int, tn: int) -> Dict[str, Any]:
    """
    Standard confusion-matrix derivations. Rates are None rather than 0.0 when
    the denominator is empty, so an absent measurement is not reported as a
    perfect score.
    """
    material_total = tp + fn
    immaterial_total = fp + tn
    flagged_total = tp + fp

    recall = tp / material_total if material_total else None
    fpr = fp / immaterial_total if immaterial_total else None
    precision = tp / flagged_total if flagged_total else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall and (precision + recall) > 0
        else None
    )

    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "material_events_total": material_total,
        "immaterial_events_total": immaterial_total,
        # The headline: "Material Event Detection Rate".
        "detection_rate_recall": round(recall, 4) if recall is not None else None,
        "false_positive_rate": round(fpr, 4) if fpr is not None else None,
        "precision": round(precision, 4) if precision is not None else None,
        "f1_score": round(f1, 4) if f1 is not None else None,
        "specificity": round(tn / immaterial_total, 4) if immaterial_total else None,
    }


# ==========================================================================
# Baseline: static periodic review
# ==========================================================================


def _review_dates(
    onboarding: datetime, window_start: datetime, window_end: datetime, interval_days: int
) -> List[datetime]:
    """
    Scheduled review dates inside the observation window.

    Anchored to the onboarding anniversary rather than a common calendar date,
    which is how periodic review is actually operated — and which spreads the
    reviews across the year instead of clustering them.
    """
    if interval_days <= 0:
        return []

    elapsed = (window_start - onboarding).days
    # First multiple of the interval that lands at or after window_start.
    k = max(1, math.ceil(elapsed / interval_days)) if elapsed > 0 else 1

    dates = []
    while True:
        candidate = onboarding + timedelta(days=k * interval_days)
        if candidate > window_end:
            break
        if candidate >= window_start:
            dates.append(candidate)
        k += 1
    return dates


def run_baseline(scenario: Scenario) -> Dict[str, Any]:
    """Scores the fixed-schedule periodic review policy over the scenario."""
    interval = scenario.config.baseline_review_interval_days

    schedules: Dict[int, List[datetime]] = {}
    for customer in scenario.customers:
        onboarding = datetime.combine(
            customer["onboarding_date"], datetime.min.time(), tzinfo=timezone.utc
        )
        schedules[customer["id"]] = _review_dates(
            onboarding, scenario.window_start, scenario.window_end, interval
        )

    tp = fp = fn = tn = 0
    latencies: List[float] = []
    miss_causes: Dict[str, int] = {}
    per_event: List[Dict[str, Any]] = []
    # Which reviews actually turned something up, for the wasted-effort metric.
    productive_reviews: set = set()

    for event in scenario.events:
        customer_id = event["true_customer_id"]
        occurred = event["occurred_at"]
        persistence = event["evidence_persistence_days"]

        detected = False
        latency: Optional[float] = None
        cause: Optional[str] = None

        if customer_id is None:
            # A decoy names nobody on the book, so no review can surface it. It
            # is correctly not acted upon.
            cause = "NOT_ON_CUSTOMER_BOOK"
        else:
            upcoming = [d for d in schedules.get(customer_id, []) if d > occurred]
            if not upcoming:
                cause = "NO_REVIEW_BEFORE_HORIZON_END"
            else:
                next_review = min(upcoming)
                gap_days = (next_review - occurred).total_seconds() / 86400.0
                if persistence is not None and gap_days > persistence:
                    # The review happens, but the evidence is no longer there to
                    # be found — a stale news story, a transaction pattern that
                    # has aged out of the monitoring window.
                    cause = "EVIDENCE_NO_LONGER_DISCOVERABLE"
                else:
                    detected = True
                    latency = gap_days
                    productive_reviews.add((customer_id, next_review))

        if event["is_material"]:
            if detected:
                tp += 1
                latencies.append(latency)
            else:
                fn += 1
                miss_causes[cause] = miss_causes.get(cause, 0) + 1
        else:
            if detected:
                fp += 1
            else:
                tn += 1

        per_event.append(
            {
                "event_id": event["event_id"],
                "detected": detected,
                "latency_days": latency,
                "miss_cause": cause if not detected else None,
            }
        )

    total_reviews = sum(len(v) for v in schedules.values())
    # A review is "wasted" when nothing material surfaced in it. This is the
    # honest cost of the periodic policy, and the number the engine competes on.
    wasted_reviews = total_reviews - len(productive_reviews)

    return {
        "policy": f"Static {interval}-day periodic review",
        **_classification_metrics(tp, fp, fn, tn),
        "latency": _latency_stats(latencies),
        "miss_causes": miss_causes,
        "workload": {
            "analyst_touches": total_reviews,
            "reviews_scheduled": total_reviews,
            "productive_reviews": len(productive_reviews),
            "wasted_reviews": wasted_reviews,
            "wasted_review_rate": round(wasted_reviews / total_reviews, 4) if total_reviews else None,
            "touches_per_material_event_caught": round(total_reviews / tp, 2) if tp else None,
        },
        "per_event": per_event,
    }


# ==========================================================================
# Engine: event-driven continuous reassessment
# ==========================================================================

# Fuzzy-match thresholds to report the resolver's behaviour at. The production
# default sits inside this range, so the report shows the chosen operating point
# in context rather than in isolation.
SWEEP_THRESHOLDS = [60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0]


def _threshold_sweep(
    candidates: List[Tuple[int, Optional[int], Dict[str, Any]]],
    operating_point: float,
) -> List[Dict[str, Any]]:
    """
    Re-decides every cached candidate at each threshold and reports the
    precision/recall trade-off.

    This is here because the first run of the harness showed the resolver
    accepting a match for **every** off-book decoy at the production threshold.
    Rather than quietly retuning the threshold and reporting the improved
    number — which would be fitting the system to its own test — the sweep
    publishes the whole curve so the operating point is an explicit, reviewable
    choice.
    """
    rows = []
    for threshold in sorted({*SWEEP_THRESHOLDS, operating_point}):
        correct = wrong = missed = decoy_matched = decoy_rejected = 0

        for _event_id, true_id, candidate in candidates:
            result = EntityResolver.decide(candidate, threshold)
            matched_id = result.matched_customer_id

            if true_id is None:
                if matched_id is None:
                    decoy_rejected += 1
                else:
                    decoy_matched += 1
            elif matched_id == true_id:
                correct += 1
            elif matched_id is None:
                missed += 1
            else:
                wrong += 1

        matches_made = correct + wrong + decoy_matched
        resolvable = correct + wrong + missed
        precision = correct / matches_made if matches_made else None
        recall = correct / resolvable if resolvable else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall and (precision + recall) > 0
            else None
        )

        rows.append(
            {
                "fuzzy_threshold": threshold,
                "is_operating_point": threshold == operating_point,
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "f1_score": round(f1, 4) if f1 is not None else None,
                "correct_matches": correct,
                "wrong_customer_matches": wrong,
                "missed_matches": missed,
                "decoys_incorrectly_matched": decoy_matched,
                "decoys_correctly_rejected": decoy_rejected,
                "decoy_rejection_rate": (
                    round(decoy_rejected / (decoy_rejected + decoy_matched), 4)
                    if (decoy_rejected + decoy_matched)
                    else None
                ),
            }
        )
    return rows


def _transient_customer(spec: Dict[str, Any]) -> Customer:
    """
    A Customer instance that is never added to a session.

    Gives the production components the exact object shape they expect without
    touching the database.
    """
    customer = Customer(
        name=spec["name"],
        type=spec["type"],
        country=spec["country"],
        industry=spec["industry"],
        expected_turnover=spec["expected_turnover"],
        actual_turnover=spec["actual_turnover"],
        is_pep=spec["is_pep"],
        is_sanctioned=spec["is_sanctioned"],
        onboarding_date=spec["onboarding_date"],
    )
    customer.id = spec["id"]
    return customer


def run_engine(scenario: Scenario) -> Dict[str, Any]:
    """Scores the event-driven policy over the scenario, using the real components."""
    resolver = EntityResolver(
        fuzzy_threshold=(
            scenario.config.fuzzy_match_threshold
            if scenario.config.fuzzy_match_threshold is not None
            else settings.FUZZY_MATCH_THRESHOLD
        )
    )
    classifier = SignalClassifier()
    risk_engine = BayesianRiskEngine()

    # --- Match index, built once (see EntityResolver.resolve_against) ---
    targets: List[Dict[str, Any]] = []
    for spec in scenario.customers:
        targets.append(
            {
                "customer_id": spec["id"],
                "customer_name": spec["name"],
                "target_string": spec["name"],
                "lower_string": spec["name"].strip().lower(),
                "norm_string": normalize_name(spec["name"]),
            }
        )
    for alias in scenario.aliases:
        name = alias["alias_name"]
        targets.append(
            {
                "customer_id": alias["customer_id"],
                "customer_name": next(
                    c["name"] for c in scenario.customers if c["id"] == alias["customer_id"]
                ),
                "target_string": name,
                "lower_string": name.strip().lower(),
                "norm_string": normalize_name(name),
            }
        )

    customers: Dict[int, Customer] = {
        spec["id"]: _transient_customer(spec) for spec in scenario.customers
    }
    # Seed each customer's running log-odds from the onboarding prior, exactly as
    # the production seeder does.
    for customer in customers.values():
        prior_p, prior_lo, _ = risk_engine.calculate_prior(customer)
        customer.risk_score = prior_p
        customer.log_odds = prior_lo
        customer.risk_tier = map_probability_to_tier(prior_p)

    # --- Deduplication index, replacing the gate's SQL query ---
    # (customer_id, category) -> list of (timestamp, event_id)
    seen: Dict[Tuple[int, str], List[Tuple[datetime, int]]] = {}

    def duplicate_lookup(customer_id: int, category: str, cutoff: datetime, exclude_id: int) -> bool:
        for timestamp, event_id in seen.get((customer_id, category), ()):
            if event_id != exclude_id and timestamp >= cutoff:
                return True
        return False

    gate = MaterialityGate(
        confidence_threshold=settings.MATERIALITY_CONFIDENCE_THRESHOLD,
        dedup_window_hours=settings.DEDUPLICATION_WINDOW_HOURS,
        duplicate_lookup=duplicate_lookup,
    )

    tp = fp = fn = tn = 0
    alert_tp = alert_fp = alert_fn = alert_tn = 0
    latencies: List[float] = []
    miss_causes: Dict[str, int] = {}
    false_positive_causes: Dict[str, int] = {}
    per_event: List[Dict[str, Any]] = []
    alerts_raised = 0

    # Entity-resolution scoring, tallied on the same pass.
    er_correct = er_wrong = er_missed = er_decoy_matched = er_decoy_rejected = 0
    er_by_perturbation: Dict[str, Dict[str, int]] = {}

    pipeline_micros: List[float] = []

    # Cached scoring candidates, so the threshold sweep below can re-decide
    # without re-scoring 5,000 × |targets| string pairs.
    candidates: List[Tuple[int, Optional[int], Dict[str, Any]]] = []

    for event_spec in scenario.events:
        started = time.perf_counter()

        # --- Stage 1: entity resolution (real matcher, prebuilt index) ---
        candidate = resolver.score_against(event_spec["observed_entity_name"], targets)
        resolution = resolver.decide(candidate, resolver.fuzzy_threshold)
        candidates.append((event_spec["event_id"], event_spec["true_customer_id"], candidate))
        matched_id = resolution.matched_customer_id
        true_id = event_spec["true_customer_id"]

        perturbation = event_spec["perturbation"]
        bucket = er_by_perturbation.setdefault(
            perturbation, {"correct": 0, "wrong": 0, "missed": 0, "total": 0}
        )
        bucket["total"] += 1

        if true_id is None:
            if matched_id is None:
                er_decoy_rejected += 1
            else:
                er_decoy_matched += 1
                bucket["wrong"] += 1
        elif matched_id == true_id:
            er_correct += 1
            bucket["correct"] += 1
        elif matched_id is None:
            er_missed += 1
            bucket["missed"] += 1
        else:
            er_wrong += 1
            bucket["wrong"] += 1

        # --- Stage 2: classification (real classifier decides category/severity) ---
        category, severity, _ = classifier.classify(
            event_type=event_spec["event_type"],
            raw_payload={"source_feed": event_spec["source"]},
            source=event_spec["source"],
        )

        # --- Stage 3: materiality gate + Bayesian scoring (real, non-persisting) ---
        transient_event = Event(
            entity_name=event_spec["observed_entity_name"],
            event_type=event_spec["event_type"],
            category=category,
            severity=severity,
            source=event_spec["source"],
            raw_payload={},
            matched_customer_id=matched_id,
            match_confidence=resolution.confidence_score,
            match_method=resolution.match_method,
        )
        transient_event.id = event_spec["event_id"]
        transient_event.created_at = event_spec["occurred_at"]

        matched_customer = customers.get(matched_id) if matched_id else None
        result = gate.evaluate(
            event=transient_event,
            customer=matched_customer,
            db=None,  # unused: duplicate_lookup and persist=False remove every query
            persist=False,
        )

        if matched_id:
            seen.setdefault((matched_id, category), []).append(
                (event_spec["occurred_at"], event_spec["event_id"])
            )

        pipeline_micros.append((time.perf_counter() - started) * 1_000_000)

        # A reassessment counts only when it landed on the *right* customer. A
        # material event scored against the wrong entity is not a detection.
        reassessed_correctly = bool(result.is_material) and matched_id == true_id and true_id is not None
        reassessed_at_all = bool(result.is_material)

        # Detection latency is bounded below by how fast the feed delivers.
        latency_days = event_spec["feed_lag_hours"] / 24.0

        if event_spec["is_material"]:
            if reassessed_correctly:
                tp += 1
                latencies.append(latency_days)
            else:
                fn += 1
                cause = (
                    result.suppression_cause
                    or ("MATCHED_WRONG_CUSTOMER" if matched_id and matched_id != true_id else "UNKNOWN")
                )
                miss_causes[cause] = miss_causes.get(cause, 0) + 1
        else:
            if reassessed_at_all:
                fp += 1
                cause = (
                    "MATCHED_ENTITY_NOT_ON_BOOK" if true_id is None
                    else "MATCHED_WRONG_CUSTOMER" if matched_id != true_id
                    else f"GATE_PASSED_{category}_{severity}"
                )
                false_positive_causes[cause] = false_positive_causes.get(cause, 0) + 1
            else:
                tn += 1

        # Alert-level accounting: not every reassessment surfaces to an analyst.
        alerted = bool(result.alert_generated) and reassessed_at_all
        if alerted:
            alerts_raised += 1
        if event_spec["is_material"]:
            if alerted and matched_id == true_id:
                alert_tp += 1
            else:
                alert_fn += 1
        else:
            if alerted:
                alert_fp += 1
            else:
                alert_tn += 1

        per_event.append(
            {
                "event_id": event_spec["event_id"],
                "detected": reassessed_correctly,
                "latency_days": latency_days if reassessed_correctly else None,
                "alerted": alerted,
                "engine_category": category,
                "engine_severity": severity,
                "suppression_cause": result.suppression_cause,
            }
        )

    er_matches_made = er_correct + er_wrong + er_decoy_matched
    er_resolvable = er_correct + er_wrong + er_missed

    entity_resolution = {
        "precision": round(er_correct / er_matches_made, 4) if er_matches_made else None,
        "recall": round(er_correct / er_resolvable, 4) if er_resolvable else None,
        "f1_score": None,  # filled below
        "correct_matches": er_correct,
        "wrong_customer_matches": er_wrong,
        "missed_matches": er_missed,
        "decoys_correctly_rejected": er_decoy_rejected,
        "decoys_incorrectly_matched": er_decoy_matched,
        "decoy_rejection_rate": (
            round(er_decoy_rejected / (er_decoy_rejected + er_decoy_matched), 4)
            if (er_decoy_rejected + er_decoy_matched)
            else None
        ),
        "resolvable_events": er_resolvable,
        "matches_attempted": er_matches_made,
        "by_perturbation": {
            key: {
                **value,
                "accuracy": round(value["correct"] / value["total"], 4) if value["total"] else None,
            }
            for key, value in sorted(er_by_perturbation.items())
        },
    }
    if entity_resolution["precision"] and entity_resolution["recall"]:
        p, r = entity_resolution["precision"], entity_resolution["recall"]
        entity_resolution["f1_score"] = round(2 * p * r / (p + r), 4)

    entity_resolution["threshold_sensitivity"] = _threshold_sweep(
        candidates, operating_point=resolver.fuzzy_threshold
    )
    entity_resolution["operating_threshold"] = resolver.fuzzy_threshold

    return {
        "policy": "Event-driven continuous reassessment (Bayesian log-odds)",
        **_classification_metrics(tp, fp, fn, tn),
        "latency": _latency_stats(latencies),
        "miss_causes": miss_causes,
        "false_positive_causes": dict(
            sorted(false_positive_causes.items(), key=lambda kv: -kv[1])
        ),
        "alert_level": {
            **_classification_metrics(alert_tp, alert_fp, alert_fn, alert_tn),
            "note": (
                "A material event can be reassessed without surfacing an alert: the alert "
                "rule fires on a tier boundary crossing or a HIGH/CRITICAL severity event, "
                "so lower-severity reassessments update the score silently."
            ),
        },
        "workload": {
            "analyst_touches": alerts_raised,
            "alerts_raised": alerts_raised,
            "touches_per_material_event_caught": round(alerts_raised / tp, 2) if tp else None,
        },
        "measured_pipeline_latency": {
            "mean_microseconds": round(statistics.fmean(pipeline_micros), 1) if pipeline_micros else None,
            "median_microseconds": round(statistics.median(pipeline_micros), 1) if pipeline_micros else None,
            "p99_microseconds": _percentile(pipeline_micros, 0.99),
            "note": (
                "In-process compute time for resolution + classification + gate + scoring, "
                "excluding database I/O. Reported separately from detection latency, which "
                "is dominated by feed delivery rather than compute."
            ),
        },
        "entity_resolution": entity_resolution,
        "per_event": per_event,
    }


# ==========================================================================
# Chart shaping
# ==========================================================================


def _bucket_latencies(latencies: List[float]) -> Dict[str, int]:
    counts = {label: 0 for label, _, _ in LATENCY_BUCKETS}
    for value in latencies:
        for label, low, high in LATENCY_BUCKETS:
            # First bucket is inclusive at 0; the rest are (low, high].
            if (value <= high and value > low) or (low == 0.0 and value <= high):
                counts[label] += 1
                break
    return counts


def _build_chart_data(
    scenario: Scenario, baseline: Dict[str, Any], engine: Dict[str, Any]
) -> Dict[str, Any]:
    """Pre-shapes the series the report renders, so the frontend does no analysis."""
    baseline_latencies = [
        e["latency_days"] for e in baseline["per_event"] if e["latency_days"] is not None
    ]
    engine_latencies = [
        e["latency_days"] for e in engine["per_event"] if e["latency_days"] is not None
    ]

    # Grouped bar: what happened to the material events, and what noise came with them.
    events_caught = [
        {
            "outcome": "Material caught",
            "baseline": baseline["true_positives"],
            "engine": engine["true_positives"],
        },
        {
            "outcome": "Material missed",
            "baseline": baseline["false_negatives"],
            "engine": engine["false_negatives"],
        },
        {
            "outcome": "False positives",
            "baseline": baseline["false_positives"],
            "engine": engine["false_positives"],
        },
    ]

    # Latency histogram, baseline vs engine.
    baseline_buckets = _bucket_latencies(baseline_latencies)
    engine_buckets = _bucket_latencies(engine_latencies)
    latency_distribution = [
        {
            "bucket": label,
            "baseline": baseline_buckets[label],
            "engine": engine_buckets[label],
        }
        for label, _, _ in LATENCY_BUCKETS
    ]

    # Cumulative detection curve: share of all material events detected within N
    # days of occurring. The clearest single picture of the difference.
    total_material = len(scenario.material_events)
    cumulative = []
    for day in CUMULATIVE_CURVE_DAYS:
        baseline_hits = sum(1 for v in baseline_latencies if v <= day)
        engine_hits = sum(1 for v in engine_latencies if v <= day)
        cumulative.append(
            {
                "days": day,
                "baseline": round(100.0 * baseline_hits / total_material, 2) if total_material else 0.0,
                "engine": round(100.0 * engine_hits / total_material, 2) if total_material else 0.0,
            }
        )

    # Median detection delay by event category, so the gap can be seen to hold
    # across categories rather than being driven by one of them.
    engine_index = {e["event_id"]: e for e in engine["per_event"]}
    baseline_index = {e["event_id"]: e for e in baseline["per_event"]}
    by_category: Dict[str, Dict[str, List[float]]] = {}
    for event in scenario.material_events:
        slot = by_category.setdefault(event["category"], {"baseline": [], "engine": []})
        for name, index in (("baseline", baseline_index), ("engine", engine_index)):
            latency = index.get(event["event_id"], {}).get("latency_days")
            if latency is not None:
                slot[name].append(latency)

    delay_by_category = [
        {
            "category": category.replace("_", " ").title(),
            "baseline": round(statistics.median(values["baseline"]), 2) if values["baseline"] else 0.0,
            "engine": round(statistics.median(values["engine"]), 3) if values["engine"] else 0.0,
            "material_events": len(values["baseline"]) + len(values["engine"]),
        }
        for category, values in sorted(by_category.items())
    ]

    # Analyst workload — the efficiency argument.
    workload = [
        {
            "metric": "Analyst touches",
            "baseline": baseline["workload"]["analyst_touches"],
            "engine": engine["workload"]["analyst_touches"],
        },
        {
            "metric": "Touches per material event caught",
            "baseline": baseline["workload"]["touches_per_material_event_caught"] or 0,
            "engine": engine["workload"]["touches_per_material_event_caught"] or 0,
        },
    ]

    return {
        "events_caught": events_caught,
        "latency_distribution": latency_distribution,
        "cumulative_detection": cumulative,
        "delay_by_category": delay_by_category,
        "workload": workload,
        "entity_resolution_by_perturbation": [
            {
                "perturbation": key.replace("_", " ").title(),
                "accuracy": round(100.0 * (value["accuracy"] or 0.0), 2),
                "total": value["total"],
            }
            for key, value in engine["entity_resolution"]["by_perturbation"].items()
        ],
        # Precision/recall as the fuzzy threshold moves, with the current
        # operating point marked. Rendered so the threshold choice is visible
        # rather than buried in config.
        "entity_resolution_threshold_sweep": [
            {
                "threshold": row["fuzzy_threshold"],
                "precision": round(100.0 * (row["precision"] or 0.0), 2),
                "recall": round(100.0 * (row["recall"] or 0.0), 2),
                "f1": round(100.0 * (row["f1_score"] or 0.0), 2),
                "decoy_rejection": round(100.0 * (row["decoy_rejection_rate"] or 0.0), 2),
                "is_operating_point": row["is_operating_point"],
            }
            for row in engine["entity_resolution"]["threshold_sensitivity"]
        ],
    }


# ==========================================================================
# Orchestration
# ==========================================================================


def run_evaluation(
    db: Session,
    config: Optional[ScenarioConfig] = None,
    actor: str = "SYSTEM",
    actor_role: str = "SYSTEM",
) -> EvaluationRun:
    """
    Generates the scenario, runs both policies, persists the report, and audits
    the run. Returns the stored EvaluationRun.
    """
    config = config or ScenarioConfig(
        num_customers=settings.EVAL_NUM_CUSTOMERS,
        num_events=settings.EVAL_NUM_EVENTS,
        horizon_days=settings.EVAL_HORIZON_DAYS,
        baseline_review_interval_days=settings.BASELINE_REVIEW_INTERVAL_DAYS,
        random_seed=settings.EVAL_RANDOM_SEED,
    )

    started = time.perf_counter()

    logger.info("Generating scenario: %s", config.as_dict())
    scenario = generate_scenario(config)

    logger.info("Running baseline (%s-day periodic review)…", config.baseline_review_interval_days)
    baseline = run_baseline(scenario)

    logger.info("Running engine (event-driven)…")
    engine = run_engine(scenario)

    chart_data = _build_chart_data(scenario, baseline, engine)
    runtime = round(time.perf_counter() - started, 3)

    # Headline deltas, computed once here so the report and the README quote the
    # same numbers.
    def delta(engine_value: Optional[float], baseline_value: Optional[float]) -> Optional[float]:
        if engine_value is None or baseline_value is None:
            return None
        return round(engine_value - baseline_value, 4)

    baseline_median = baseline["latency"]["median_days"]
    engine_median = engine["latency"]["median_days"]

    deltas = {
        "detection_rate_gain": delta(
            engine["detection_rate_recall"], baseline["detection_rate_recall"]
        ),
        "false_positive_rate_change": delta(
            engine["false_positive_rate"], baseline["false_positive_rate"]
        ),
        "median_latency_reduction_days": (
            round(baseline_median - engine_median, 3)
            if baseline_median is not None and engine_median is not None
            else None
        ),
        "median_latency_speedup_factor": (
            round(baseline_median / engine_median, 1)
            if baseline_median and engine_median
            else None
        ),
        "additional_material_events_caught": engine["true_positives"] - baseline["true_positives"],
        "analyst_touch_reduction": (
            baseline["workload"]["analyst_touches"] - engine["workload"]["analyst_touches"]
        ),
    }

    # per_event is large (5,000 rows per arm) and only needed during scoring —
    # strip it before persisting so the stored report stays queryable.
    baseline_stored = {k: v for k, v in baseline.items() if k != "per_event"}
    engine_stored = {k: v for k, v in engine.items() if k != "per_event"}

    metrics = {
        "baseline": baseline_stored,
        "engine": engine_stored,
        "entity_resolution": engine["entity_resolution"],
        "deltas": deltas,
        "ground_truth": {
            "total_events": len(scenario.events),
            "material_events": len(scenario.material_events),
            "immaterial_events": len(scenario.immaterial_events),
            "material_rate": round(len(scenario.material_events) / len(scenario.events), 4),
            "hard_case_events": sum(1 for e in scenario.events if e["is_hard_case"]),
            "decoy_events": sum(1 for e in scenario.events if e["true_customer_id"] is None),
        },
    }

    record = EvaluationRun(
        label="baseline_vs_engine",
        random_seed=config.random_seed,
        num_customers=config.num_customers,
        num_events=config.num_events,
        horizon_days=config.horizon_days,
        config={**config.as_dict(), "assumptions": scenario.assumptions},
        metrics=metrics,
        chart_data=chart_data,
        runtime_seconds=runtime,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    audit.record(
        db,
        entity_type="SYSTEM",
        entity_id=record.id,
        action="EVALUATION_RUN_COMPLETED",
        actor=actor,
        actor_role=actor_role,
        details={
            "evaluation_run_id": record.id,
            "config": config.as_dict(),
            "engine_detection_rate": engine["detection_rate_recall"],
            "baseline_detection_rate": baseline["detection_rate_recall"],
            "engine_false_positive_rate": engine["false_positive_rate"],
            "baseline_false_positive_rate": baseline["false_positive_rate"],
            "engine_median_latency_days": engine_median,
            "baseline_median_latency_days": baseline_median,
            "entity_resolution_precision": engine["entity_resolution"]["precision"],
            "entity_resolution_recall": engine["entity_resolution"]["recall"],
            "runtime_seconds": runtime,
        },
    )

    logger.info("Evaluation complete in %.2fs (run id %s)", runtime, record.id)
    return record
