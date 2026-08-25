"""
Labeled synthetic scenario generator.

The point of this module is that **the ground truth is decided here, from a
policy definition, before either system sees the data** — not derived from what
either system happens to do. Every event carries:

*   ``is_material``          — should this event have triggered a reassessment?
*   ``ground_truth_reason``  — why, in words, so a reviewer can audit the label.
*   ``true_customer_id``     — which customer it genuinely concerns (None for a
                               decoy that concerns nobody on the book).
*   ``evidence_persistence_days`` — how long the evidence stays discoverable.

## How the labels are decided

Materiality is defined from the *obligation*, following the MLR 2017 / JMLSG
notion of a trigger event: a change in circumstances that could alter the
customer's risk profile or the adequacy of existing due diligence. It is
deliberately **not** defined as "whatever the engine's severity filter passes",
because that would make the evaluation circular and guarantee a perfect score.

The consequence is that several archetypes sit across the engine's decision
boundaries and are expected to be scored wrongly by it. They are kept in, and
flagged ``is_hard_case``, so the report can attribute errors to a cause:

| Archetype | Ground truth | What the engine does |
|---|---|---|
| ``DIRECTOR_CHANGE`` | not material | classifier rates it CORPORATE_CHANGE/MEDIUM, so it clears the gate — the largest single false-positive source, and the volume driver |
| ``NEGATIVE_NEWS`` | not material | MEDIUM adverse media clears the gate and scores |
| ``TRANSACTION_DEVIATION`` | not material | MEDIUM anomaly clears the gate |
| ``HIGH_RISK_JURISDICTION_EXPOSURE`` on a customer already in that jurisdiction | not material | the gate has no "already priced in" test |
| decoy entity names | not material | nothing on the book to reassess; a match here is a false positive |
| any material event on a name the resolver fails to match | material | never reaches the gate — a miss caused by Phase 1, not Phase 2 |

## Evidence persistence

Point-in-time evidence stops being discoverable. A transaction burst is visible
in monitoring for weeks, not quarters; a news story gets superseded. A sanctions
listing or a companies-register filing, by contrast, is a permanent record.
``evidence_persistence_days`` encodes that, and it is the mechanism by which a
periodic review genuinely misses things rather than an arbitrary penalty. It is
reported as an explicit assumption in the output config so the numbers can be
recomputed under a different assumption.
"""
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Reuse the same reference data the production seeder uses, so evaluation
# customers are drawn from the same distribution as real ones.
from app.seed import COUNTRIES, CORPORATE_SUFFIXES, INDUSTRIES

# --------------------------------------------------------------------------
# Feed latency — how long after an event occurs the engine can first see it.
# The engine cannot beat its slowest input, so this is charged against it.
# --------------------------------------------------------------------------
SOURCE_FEED_LAG_HOURS: Dict[str, float] = {
    "OpenSanctions_API": 2.0,
    "FCA_Register": 6.0,
    "NewsAPI": 1.0,
    "UK_Companies_House": 24.0,
    "Internal_Transaction_Monitoring": 0.25,
}

# Never expires — a matter of permanent public record.
PERMANENT = None


@dataclass(frozen=True)
class EventArchetype:
    """One kind of event, with its ground-truth label fixed up front."""
    event_type: str
    category: str
    severity: str
    source: str
    is_material: bool
    ground_truth_reason: str
    # None = permanent record; an int = days the evidence stays discoverable.
    evidence_persistence_days: Optional[int]
    # Relative sampling weight.
    weight: float
    # True for the deliberately-hard cases, so the report can break them out.
    is_hard_case: bool = False
    # Restricts the archetype to Corporate or Individual customers when set.
    applies_to: Optional[str] = None


# Every event_type below is one the production classifier rule table
# (app/classifier_rules.json) already recognises. That matters: the engine run
# classifies each event with the real SignalClassifier, so if the scenario
# invented event types the classifier had never seen, the engine's errors would
# measure a config gap rather than the algorithm. The `category` and `severity`
# fields here are descriptive only — the engine derives its own.
ARCHETYPES: List[EventArchetype] = [
    # ---------------- Genuinely material ----------------
    EventArchetype(
        "SANCTIONS_UPDATE", "SANCTIONS_MATCH", "CRITICAL", "OpenSanctions_API",
        True, "Designation on a sanctions list — mandatory immediate reassessment and freeze.",
        PERMANENT, 2.6,
    ),
    EventArchetype(
        "PEP_LISTING", "PEP_STATUS", "HIGH", "OpenSanctions_API",
        True, "Newly identified politically exposed person — the EDD obligation is triggered.",
        PERMANENT, 2.2,
    ),
    EventArchetype(
        "PEP_ASSOCIATE", "PEP_STATUS", "MEDIUM", "OpenSanctions_API",
        True, "Close associate or family member of a PEP — in scope for EDD under JMLSG.",
        PERMANENT, 1.6,
    ),
    EventArchetype(
        "CLIENT_MONEY_REVOCATION", "REGULATORY_CHANGE", "CRITICAL", "FCA_Register",
        True, "Client-money permission withdrawn — the WealthTek pattern; directly alters counterparty risk.",
        PERMANENT, 1.1,
    ),
    EventArchetype(
        "FCA_AUTHORIZATION_CHANGE", "REGULATORY_CHANGE", "HIGH", "FCA_Register",
        True, "Firm-specific regulatory permission varied or withdrawn.",
        PERMANENT, 1.8,
    ),
    EventArchetype(
        "FRAUD_ALLEGATION", "ADVERSE_MEDIA", "HIGH", "NewsAPI",
        True, "Credible, entity-specific fraud allegation in reputable media.",
        30, 3.0,
    ),
    EventArchetype(
        "CRIMINAL_CHARGES", "LAW_ENFORCEMENT", "CRITICAL", "NewsAPI",
        True, "Criminal charges brought against the customer or its officers.",
        45, 1.3,
    ),
    EventArchetype(
        "POLICE_RAID", "LAW_ENFORCEMENT", "CRITICAL", "NewsAPI",
        True, "Premises searched under warrant — the Stunt & Co pattern.",
        45, 0.9,
    ),
    EventArchetype(
        "TRANSACTION_SPIKE", "TRANSACTION_ANOMALY", "HIGH", "Internal_Transaction_Monitoring",
        True, "Turnover materially exceeds the profile agreed at onboarding.",
        45, 3.4,
    ),
    EventArchetype(
        "ISOLATION_FOREST_ANOMALY", "TRANSACTION_ANOMALY", "HIGH", "Internal_Transaction_Monitoring",
        True, "Statistically anomalous payment pattern flagged by transaction monitoring.",
        45, 1.9,
    ),
    EventArchetype(
        "UBO_CHANGE", "CORPORATE_CHANGE", "HIGH", "UK_Companies_House",
        True, "Change of beneficial ownership — a textbook trigger event under MLR 2017 reg 27.",
        PERMANENT, 2.4, applies_to="Corporate",
    ),
    EventArchetype(
        "HIGH_RISK_JURISDICTION_EXPOSURE", "GEOGRAPHIC_EXPOSURE", "MEDIUM", "Internal_Transaction_Monitoring",
        True, "New counterparty exposure to a FATF-listed jurisdiction.",
        60, 1.8,
    ),

    # ---------------- Genuinely not material ----------------
    # DIRECTOR_CHANGE is the volume driver, and the engine's classifier rates it
    # MEDIUM — so it is also the engine's main false-positive source. That is the
    # alert-fatigue trade-off this evaluation is meant to expose, not hide.
    EventArchetype(
        "DIRECTOR_CHANGE", "CORPORATE_CHANGE", "MEDIUM", "UK_Companies_House",
        False, "Routine non-controlling board appointment or resignation — no change to the risk profile.",
        PERMANENT, 7.5, is_hard_case=True, applies_to="Corporate",
    ),
    EventArchetype(
        "NEGATIVE_NEWS", "ADVERSE_MEDIA", "MEDIUM", "NewsAPI",
        False, "Minor local coverage — a commercial dispute or service complaint, not financial crime.",
        21, 6.0, is_hard_case=True,
    ),
    EventArchetype(
        "TRANSACTION_DEVIATION", "TRANSACTION_ANOMALY", "MEDIUM", "Internal_Transaction_Monitoring",
        False, "Volume variance inside the tolerance agreed at onboarding — seasonal, not suspicious.",
        45, 5.0, is_hard_case=True,
    ),
    EventArchetype(
        "REGISTERED_ADDRESS_CHANGE", "CORPORATE_CHANGE", "LOW", "UK_Companies_House",
        False, "Registered-office relocation within the same jurisdiction.",
        PERMANENT, 6.5, applies_to="Corporate",
    ),
    EventArchetype(
        "ROUTINE_FILING", "CORPORATE_CHANGE", "LOW", "UK_Companies_House",
        False, "Annual confirmation statement filed on time.",
        PERMANENT, 8.5, applies_to="Corporate",
    ),
    EventArchetype(
        "ROUTINE_ANNOUNCEMENT", "CORPORATE_CHANGE", "LOW", "NewsAPI",
        False, "Product launch or hiring announcement — no financial-crime relevance.",
        14, 6.5,
    ),
    EventArchetype(
        "UNVERIFIED_NEWS", "ADVERSE_MEDIA", "LOW", "NewsAPI",
        False, "Unsourced blog or aggregator item that fails the corroboration threshold.",
        14, 4.5,
    ),
    EventArchetype(
        "LOW_RISK_JURISDICTION_EXPOSURE", "GEOGRAPHIC_EXPOSURE", "LOW", "Internal_Transaction_Monitoring",
        False, "New counterparty in an equivalent-standards jurisdiction.",
        60, 4.0,
    ),
]


# --------------------------------------------------------------------------
# Entity-name perturbation — the observed name is rarely the name on file
# --------------------------------------------------------------------------

SUFFIX_SWAPS = {
    "Ltd": "Limited", "Limited": "Ltd", "Inc": "Incorporated",
    "Incorporated": "Inc", "Corp": "Corporation", "Corporation": "Corp",
    "PLC": "Plc", "LLC": "L.L.C.", "Holdings": "Holding Group",
    "Group": "Grp", "Services": "Svcs", "GmbH": "GmbH & Co KG",
}

PERTURBATION_WEIGHTS = [
    ("EXACT", 0.38),
    ("LEGAL_SUFFIX_VARIANT", 0.20),
    ("CASE_AND_PUNCTUATION", 0.08),
    ("ALIAS", 0.09),
    ("TYPO", 0.13),
    ("ABBREVIATION", 0.04),
    ("DECOY", 0.08),  # concerns nobody on the book
]


@dataclass
class ScenarioConfig:
    num_customers: int = 1000
    num_events: int = 5000
    horizon_days: int = 365
    baseline_review_interval_days: int = 90
    random_seed: int = 42
    # None = use the production default from settings. Exposed so the threshold
    # recommendation the sweep produces can be re-run and checked rather than
    # taken on trust.
    fuzzy_match_threshold: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "num_customers": self.num_customers,
            "num_events": self.num_events,
            "horizon_days": self.horizon_days,
            "baseline_review_interval_days": self.baseline_review_interval_days,
            "random_seed": self.random_seed,
            "fuzzy_match_threshold": self.fuzzy_match_threshold,
        }


@dataclass
class Scenario:
    config: ScenarioConfig
    customers: List[Dict[str, Any]]
    aliases: List[Dict[str, Any]]
    events: List[Dict[str, Any]]
    window_start: datetime
    window_end: datetime
    assumptions: Dict[str, Any] = field(default_factory=dict)

    @property
    def material_events(self) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["is_material"]]

    @property
    def immaterial_events(self) -> List[Dict[str, Any]]:
        return [e for e in self.events if not e["is_material"]]


def _apply_typo(name: str, rng: random.Random) -> str:
    """Single-character corruption: the kind a manual data-entry step produces."""
    if len(name) < 5:
        return name
    mode = rng.choice(["transpose", "drop", "double"])
    i = rng.randrange(1, len(name) - 2)
    if mode == "transpose":
        return name[:i] + name[i + 1] + name[i] + name[i + 2:]
    if mode == "drop":
        return name[:i] + name[i + 1:]
    return name[:i] + name[i] + name[i:]


def _abbreviate(name: str, rng: random.Random) -> str:
    words = name.split()
    if len(words) < 2:
        return name
    # Truncate the middle word, keeping the first word and the legal suffix.
    idx = 1 if len(words) == 2 else rng.randrange(1, len(words) - 1)
    if len(words[idx]) > 4:
        words[idx] = words[idx][:4]
    return " ".join(words)


def _perturb(
    name: str,
    aliases: List[str],
    mode: str,
    rng: random.Random,
) -> str:
    if mode == "EXACT":
        return name
    if mode == "CASE_AND_PUNCTUATION":
        return name.upper().replace(" ", ", ", 1) if rng.random() < 0.5 else name.lower()
    if mode == "ALIAS":
        return rng.choice(aliases) if aliases else name
    if mode == "TYPO":
        return _apply_typo(name, rng)
    if mode == "ABBREVIATION":
        return _abbreviate(name, rng)
    if mode == "LEGAL_SUFFIX_VARIANT":
        words = name.split()
        if words and words[-1] in SUFFIX_SWAPS:
            return " ".join(words[:-1] + [SUFFIX_SWAPS[words[-1]]])
        return f"{name} Limited"
    return name


def _weighted_choice(rng: random.Random, options: List[tuple]) -> Any:
    total = sum(w for _, w in options)
    roll = rng.random() * total
    upto = 0.0
    for value, weight in options:
        upto += weight
        if roll <= upto:
            return value
    return options[-1][0]


def generate_scenario(config: ScenarioConfig) -> Scenario:
    """
    Builds the scenario deterministically from ``config.random_seed``.

    Same seed in, byte-identical scenario out — so a reported number can be
    reproduced rather than merely believed.
    """
    rng = random.Random(config.random_seed)

    window_end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=config.horizon_days)

    # ---------------- Customers ----------------
    customers: List[Dict[str, Any]] = []
    aliases: List[Dict[str, Any]] = []
    # The name space has to be large enough that near-duplicates are occasional
    # rather than structural. A small combinatorial pool would make every
    # customer look like every other one, and the resulting entity-resolution
    # numbers would measure the generator rather than the resolver.
    first_parts = [
        "Northwind", "Meridian", "Kestrel", "Aldgate", "Brightwater", "Copperfield",
        "Drayton", "Eastvale", "Fenchurch", "Granville", "Harlow", "Ironbridge",
        "Jubilee", "Kingsmead", "Lancaster", "Marlborough", "Newbury", "Oakhaven",
        "Pembroke", "Quayside", "Ravensworth", "Sandringham", "Thornbury", "Upminster",
        "Vantage", "Westbourne", "Yarrow", "Zenith", "Ashcroft", "Belgrave",
        "Cavendish", "Dunwoody", "Ellingham", "Foxglove", "Glenmorris", "Hartsmere",
        "Inverleith", "Jerrenford", "Kilbride", "Larkspur", "Munstead", "Nethercott",
        "Ostravia", "Pinewold", "Quillon", "Rookwood", "Silverdale", "Tamarind",
        "Underhill", "Vellacott", "Wexford", "Yelverton", "Amberley", "Borthwick",
        "Calderstone", "Dovecote", "Edgewarth", "Faircross", "Grimsby", "Holloway",
    ]
    second_parts = [
        "Capital", "Partners", "Trading", "Ventures", "Resources", "Logistics",
        "Assets", "Industries", "Commodities", "Bullion", "Digital", "Maritime",
        "Aviation", "Metals", "Energy", "Properties", "Advisory", "Securities",
        "Analytics", "Chartering", "Consolidated", "Distribution", "Engineering",
        "Fiduciary", "Freight", "Investments", "Laboratories", "Managements",
        "Nominees", "Petrochemical", "Pharma", "Recycling", "Refining", "Robotics",
        "Shipping", "Textiles", "Underwriting", "Warehousing", "Wholesale", "Works",
    ]
    third_parts = [
        "Europe", "Global", "International", "UK", "Overseas", "Atlantic",
        "Continental", "Pacific", "Nordic", "Iberia", "Levant", "Anglo",
    ]
    given_names = [
        "Adaeze", "Bilal", "Cerys", "Dmitri", "Eleni", "Farhan", "Gudrun", "Hiroshi",
        "Ilaria", "Jonas", "Kalinda", "Lukas", "Mireille", "Nadia", "Osei", "Priya",
        "Quentin", "Rafael", "Saoirse", "Tomas", "Ulrike", "Viktor", "Wanjiku", "Xiulan",
        "Yusuf", "Zofia", "Anouk", "Bahar", "Caoimhe", "Dilnoza", "Emeka", "Fiorella",
        "Gethin", "Hanan", "Iulia", "Jarrah", "Kwabena", "Leilani", "Matteo", "Ngozi",
    ]
    family_names = [
        "Abara", "Bergstrom", "Carvalho", "Duarte", "Eriksen", "Fontaine", "Gallagher",
        "Haddad", "Ivanova", "Jankowski", "Kowalczyk", "Lindgren", "Moreau", "Nakamura",
        "Okonkwo", "Pereira", "Quintero", "Rasmussen", "Silva", "Thorsen", "Ueda",
        "Vasquez", "Whitcombe", "Yamamoto", "Zubairu", "Achterberg", "Bonnaire",
        "Csikos", "Delacroix", "Ekwueme", "Fitzsimmons", "Grigorescu", "Hallgrimsson",
        "Ishikawa", "Jovanovic", "Kaczmarek", "Lindqvist", "Mbeki", "Novotny",
        "Oyelaran", "Papadakis", "Rautavaara", "Sandoval", "Tuominen", "Villanueva",
    ]
    middle_initials = list("ABCDEFGHJKLMNPRSTVW")

    for customer_id in range(1, config.num_customers + 1):
        is_corporate = rng.random() < 0.62
        if is_corporate:
            parts = [rng.choice(first_parts), rng.choice(second_parts)]
            # ~30% carry a regional qualifier, which is where a lot of real
            # near-duplication comes from ("… Trading Ltd" vs "… Trading Europe Ltd").
            if rng.random() < 0.30:
                parts.append(rng.choice(third_parts))
            parts.append(rng.choice(CORPORATE_SUFFIXES))
            name = " ".join(parts)
        else:
            if rng.random() < 0.35:
                name = f"{rng.choice(given_names)} {rng.choice(middle_initials)} {rng.choice(family_names)}"
            else:
                name = f"{rng.choice(given_names)} {rng.choice(family_names)}"

        expected_turnover = round(rng.uniform(50_000, 250_000_000), 2)
        onboarding = (window_start - timedelta(days=rng.randint(30, 1800))).date()

        customer = {
            "id": customer_id,
            "name": name,
            "type": "Corporate" if is_corporate else "Individual",
            "country": rng.choice(COUNTRIES),
            "industry": rng.choice(INDUSTRIES),
            "expected_turnover": expected_turnover,
            "actual_turnover": round(expected_turnover * rng.uniform(0.8, 1.25), 2),
            "is_pep": rng.random() < 0.035,
            "is_sanctioned": rng.random() < 0.015,
            "onboarding_date": onboarding,
        }
        customers.append(customer)

        # ~30% of corporates carry a trading name, matching the production seeder.
        if is_corporate and rng.random() < 0.30:
            words = name.split()
            aliases.append({
                "customer_id": customer_id,
                "alias_name": f"{words[0]} {words[1]} Global",
                "alias_type": "TRADING_NAME",
            })

    aliases_by_customer: Dict[int, List[str]] = {}
    for alias in aliases:
        aliases_by_customer.setdefault(alias["customer_id"], []).append(alias["alias_name"])

    corporate_ids = [c["id"] for c in customers if c["type"] == "Corporate"]
    individual_ids = [c["id"] for c in customers if c["type"] == "Individual"]
    by_id = {c["id"]: c for c in customers}

    # High-risk customers attract disproportionately more events, as in reality.
    def event_propensity(customer: Dict[str, Any]) -> float:
        weight = 1.0
        if customer["is_sanctioned"]:
            weight += 3.0
        if customer["is_pep"]:
            weight += 1.5
        if customer["expected_turnover"] > 50_000_000:
            weight += 1.0
        return weight

    propensity = [(c["id"], event_propensity(c)) for c in customers]

    archetype_weights = [(a, a.weight) for a in ARCHETYPES]

    # ---------------- Events ----------------
    events: List[Dict[str, Any]] = []
    for event_id in range(1, config.num_events + 1):
        archetype: EventArchetype = _weighted_choice(rng, archetype_weights)

        # Pick a customer the archetype can actually apply to.
        if archetype.applies_to == "Corporate":
            candidate_id = rng.choice(corporate_ids)
        elif archetype.applies_to == "Individual":
            candidate_id = rng.choice(individual_ids) if individual_ids else rng.choice(corporate_ids)
        else:
            candidate_id = _weighted_choice(rng, propensity)

        customer = by_id[candidate_id]
        perturbation = _weighted_choice(rng, PERTURBATION_WEIGHTS)

        is_material = archetype.is_material
        reason = archetype.ground_truth_reason

        # A geographic-exposure event is only a trigger when the jurisdiction is
        # actually high risk for this customer — a UAE counterparty for a UAE
        # customer is business as usual, not a change in circumstances.
        if archetype.event_type == "HIGH_RISK_JURISDICTION_EXPOSURE":
            if customer["country"] in {"KY", "VG", "PA", "AE"}:
                is_material = False
                reason = (
                    "Counterparty jurisdiction matches the customer's own — already "
                    "priced into the onboarding assessment, not a change in circumstances."
                )

        if perturbation == "DECOY":
            # A name that resembles the book but belongs to no customer. Nothing
            # to reassess, so it cannot be material whatever its severity.
            observed_name = (
                f"{rng.choice(first_parts)} {rng.choice(second_parts)} "
                f"{rng.choice(['Nominees', 'Trustees', 'Overseas', 'International'])} "
                f"{rng.choice(CORPORATE_SUFFIXES)}"
            )
            true_customer_id = None
            is_material = False
            reason = "Entity is not on the customer book — no reassessment obligation exists."
        else:
            observed_name = _perturb(
                customer["name"],
                aliases_by_customer.get(candidate_id, []),
                perturbation,
                rng,
            )
            true_customer_id = candidate_id

        # Uniform over the window; the propensity weighting above already
        # concentrates volume on riskier customers.
        occurred_at = window_start + timedelta(
            seconds=rng.randrange(0, config.horizon_days * 86400)
        )

        events.append({
            "event_id": event_id,
            "occurred_at": occurred_at,
            "observed_entity_name": observed_name,
            "true_customer_id": true_customer_id,
            "perturbation": perturbation,
            "event_type": archetype.event_type,
            "category": archetype.category,
            "severity": archetype.severity,
            "source": archetype.source,
            "is_material": is_material,
            "ground_truth_reason": reason,
            "evidence_persistence_days": archetype.evidence_persistence_days,
            "is_hard_case": archetype.is_hard_case,
            "feed_lag_hours": SOURCE_FEED_LAG_HOURS.get(archetype.source, 24.0),
        })

    events.sort(key=lambda e: e["occurred_at"])

    return Scenario(
        config=config,
        customers=customers,
        aliases=aliases,
        events=events,
        window_start=window_start,
        window_end=window_end,
        assumptions={
            "materiality_definition": (
                "A trigger event under MLR 2017 reg 27 / JMLSG guidance: a change in "
                "circumstances capable of altering the customer's risk profile or the "
                "adequacy of existing due diligence. Defined independently of either "
                "system's decision rules."
            ),
            "evidence_persistence": (
                "Point-in-time evidence (adverse media, transaction patterns) stops being "
                "discoverable after evidence_persistence_days. Register filings and sanctions "
                "designations are permanent. This is the mechanism by which a periodic review "
                "misses events rather than an arbitrary penalty."
            ),
            "feed_lag": (
                "The engine cannot act before its feed delivers. Per-source lag is charged "
                "against the engine's detection latency: "
                + ", ".join(f"{k}={v}h" for k, v in SOURCE_FEED_LAG_HOURS.items())
            ),
            "baseline_policy": (
                f"Reassessment on a fixed {config.baseline_review_interval_days}-day cycle "
                "anchored to each customer's onboarding date, regardless of what happens in "
                "between. Detects an event if a review falls after it, within the horizon, "
                "while the evidence is still discoverable."
            ),
            "decoy_rate": f"{PERTURBATION_WEIGHTS[-1][1]:.0%} of events name an entity not on the book.",
            "name_perturbation_rate": (
                f"{1 - dict(PERTURBATION_WEIGHTS)['EXACT']:.0%} of events name the entity "
                "in some form other than exactly as recorded."
            ),
        },
    )
