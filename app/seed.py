import random
from datetime import datetime, timedelta
from faker import Faker
from sqlalchemy.orm import Session
from app import audit
from app.models import Customer, EntityAlias

fake = Faker()

INDUSTRIES = [
    "Banking & Finance", "Fintech", "Real Estate", "Defense & Aerospace",
    "Energy & Commodities", "Healthcare & Pharma", "Crypto & Digital Assets",
    "E-Commerce & Retail", "Logistics & Shipping", "Legal & Consulting",
    "Jewelry & Precious Metals", "Telecommunications", "Manufacturing"
]

HIGH_RISK_INDUSTRIES = {"Crypto & Digital Assets", "Defense & Aerospace", "Jewelry & Precious Metals", "Real Estate"}

COUNTRIES = ["GB", "US", "DE", "FR", "SG", "HK", "AE", "CH", "KY", "VG", "PA", "JP", "CA", "AU", "NL", "LU"]
HIGH_RISK_COUNTRIES = {"KY", "VG", "PA", "AE"}

CORPORATE_SUFFIXES = ["Ltd", "Limited", "Inc", "Corporation", "Corp", "LLC", "GmbH", "PLC", "Holdings", "Group", "Services"]

from app.risk_engine import PriorRiskModel, map_probability_to_tier

prior_model = PriorRiskModel()

def calculate_initial_risk(is_sanctioned: bool, is_pep: bool, industry: str, country: str, cust_type: str = "Corporate", turnover: float = 100000.0) -> tuple[float, float, str]:
    dummy_cust = Customer(
        type=cust_type,
        country=country,
        industry=industry,
        expected_turnover=turnover,
        actual_turnover=turnover,
        is_pep=is_pep,
        is_sanctioned=is_sanctioned
    )
    prob, log_odds, _ = prior_model.calculate_prior(dummy_cust)
    tier = map_probability_to_tier(prob)
    return prob, log_odds, tier


def seed_database(db: Session, target_count: int = 2000):
    existing_count = db.query(Customer).count()
    if existing_count >= target_count:
        print(f"[Seed] Database already contains {existing_count} customers. Skipping seed.")
        return

    print(f"[Seed] Seeding {target_count - existing_count} synthetic customers...")

    customers_to_create = []
    aliases_to_create = []

    # Ensure deterministic reproducible seed or randomized realistic data
    Faker.seed(42)
    random.seed(42)

    start_date = datetime.now() - timedelta(days=365 * 5)

    for i in range(target_count - existing_count):
        is_corporate = random.random() < 0.6
        cust_type = "Corporate" if is_corporate else "Individual"
        
        if is_corporate:
            company_base = fake.company().replace(",", "").replace(".", "")
            suffix = random.choice(CORPORATE_SUFFIXES)
            name = f"{company_base} {suffix}"
        else:
            name = fake.name()

        country = random.choice(COUNTRIES)
        industry = random.choice(INDUSTRIES)
        turnover = round(random.uniform(50_000, 250_000_000), 2)
        actual_turnover = round(turnover * random.uniform(0.8, 1.2), 2)
        
        is_sanctioned = random.random() < 0.015  # 1.5% sanctioned
        is_pep = random.random() < 0.035         # 3.5% PEP
        
        onboarding_date = (start_date + timedelta(days=random.randint(0, 1800))).date()
        risk_score, log_odds, risk_tier = calculate_initial_risk(
            is_sanctioned=is_sanctioned,
            is_pep=is_pep,
            industry=industry,
            country=country,
            cust_type=cust_type,
            turnover=turnover
        )

        customer = Customer(
            name=name,
            type=cust_type,
            country=country,
            industry=industry,
            expected_turnover=turnover,
            actual_turnover=actual_turnover,
            is_pep=is_pep,
            is_sanctioned=is_sanctioned,
            onboarding_date=onboarding_date,
            risk_score=risk_score,
            log_odds=log_odds,
            risk_tier=risk_tier
        )
        customers_to_create.append(customer)


    # Bulk insert customers
    db.add_all(customers_to_create)
    db.commit()

    # Create entity aliases for corporate customers
    corporate_customers = db.query(Customer).filter(Customer.type == "Corporate").all()
    for cust in corporate_customers:
        # ~30% chance of having an alias
        if random.random() < 0.30:
            words = cust.name.split()
            if len(words) >= 2:
                # Trading name alias
                trading_name = f"{words[0]} {words[1]} Global"
                aliases_to_create.append(EntityAlias(
                    customer_id=cust.id,
                    alias_name=trading_name,
                    alias_type="TRADING_NAME"
                ))
            if random.random() < 0.15:
                # Acronym alias
                acronym = "".join([w[0].upper() for w in words if w[0].isalpha()])
                if len(acronym) >= 2:
                    aliases_to_create.append(EntityAlias(
                        customer_id=cust.id,
                        alias_name=acronym,
                        alias_type="ACRONYM"
                    ))

    if aliases_to_create:
        db.add_all(aliases_to_create)
        db.commit()


    audit.record(
        db,
        entity_type="SYSTEM",
        entity_id=0,
        action="SEED_DATABASE",
        details={
            "seeded_customers": len(customers_to_create),
            "seeded_aliases": len(aliases_to_create),
        },
    )

    print(f"[Seed] Successfully seeded {len(customers_to_create)} customers and {len(aliases_to_create)} aliases.")


# Named subjects for the console's Live pipeline presets. Without these, an
# injected "WealthTek Ltd" has nothing correct to match and the resolver picks
# the nearest synthetic name instead — which makes the flagship demo look like a
# matching bug rather than a demonstration. Each entity mirrors a real FCA
# enforcement subject and carries an alias so alias matching can be shown too.
DEMO_ENTITIES = [
    {
        "name": "Barclays PLC",
        "type": "Corporate",
        "country": "GB",
        "industry": "Banking & Finance",
        "expected_turnover": 25_000_000_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [("Barclays Bank UK PLC", "LEGAL_ENTITY"), ("Barclays Bank", "TRADING_NAME"), ("Barclays", "SHORT_NAME")],
    },
    {
        "name": "WealthTek Ltd",
        "type": "Corporate",
        "country": "GB",
        "industry": "Banking & Finance",
        "expected_turnover": 42_000_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [("WealthTek LLP", "FORMER_NAME"), ("Vertem Asset Management", "TRADING_NAME")],
    },
    {
        "name": "Stunt & Co Ltd",
        "type": "Corporate",
        "country": "GB",
        "industry": "Jewelry & Precious Metals",
        "expected_turnover": 118_000_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [("Stunt and Company", "TRADING_NAME")],
    },
    {
        "name": "Meridian Bullion Trading Ltd",
        "type": "Corporate",
        "country": "AE",
        "industry": "Jewelry & Precious Metals",
        "expected_turnover": 76_500_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [("Meridian Bullion DMCC", "TRADING_NAME"), ("MBT", "ACRONYM")],
    },
    {
        "name": "Kestrel Offshore Holdings Ltd",
        "type": "Corporate",
        "country": "KY",
        "industry": "Real Estate",
        "expected_turnover": 31_000_000.0,
        "is_pep": True,
        "is_sanctioned": False,
        "aliases": [("Kestrel Offshore Group", "TRADING_NAME")],
    },
    {
        "name": "Aldgate Crypto Exchange Ltd",
        "type": "Corporate",
        "country": "GB",
        "industry": "Crypto & Digital Assets",
        "expected_turnover": 9_400_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [("Aldgate Digital", "TRADING_NAME")],
    },
    {
        "name": "Northwind Freight Services Ltd",
        "type": "Corporate",
        "country": "GB",
        "industry": "Logistics & Shipping",
        "expected_turnover": 5_200_000.0,
        "is_pep": False,
        "is_sanctioned": False,
        "aliases": [],
    },
]


def seed_demo_entities(db: Session) -> int:
    """
    Creates the named demo subjects if absent. Idempotent, and never modifies an
    existing customer — a repeated startup must not reset a risk score the demo
    has already moved.
    """
    created = 0
    for spec in DEMO_ENTITIES:
        if db.query(Customer).filter(Customer.name == spec["name"]).first():
            continue

        prob, log_odds, tier = calculate_initial_risk(
            is_sanctioned=spec["is_sanctioned"],
            is_pep=spec["is_pep"],
            industry=spec["industry"],
            country=spec["country"],
            cust_type=spec["type"],
            turnover=spec["expected_turnover"],
        )
        customer = Customer(
            name=spec["name"],
            type=spec["type"],
            country=spec["country"],
            industry=spec["industry"],
            expected_turnover=spec["expected_turnover"],
            actual_turnover=spec["expected_turnover"],
            is_pep=spec["is_pep"],
            is_sanctioned=spec["is_sanctioned"],
            onboarding_date=(datetime.now() - timedelta(days=900)).date(),
            risk_score=prob,
            log_odds=log_odds,
            risk_tier=tier,
        )
        db.add(customer)
        db.flush()  # assigns customer.id without ending the transaction

        for alias_name, alias_type in spec["aliases"]:
            db.add(
                EntityAlias(
                    customer_id=customer.id,
                    alias_name=alias_name,
                    alias_type=alias_type,
                )
            )
        created += 1

    if created:
        db.commit()
        print(f"[Seed] Created {created} named demo entities for the Live pipeline presets.")
    return created
