import random
from datetime import datetime, timedelta
from faker import Faker
from sqlalchemy.orm import Session
from app.models import Customer, EntityAlias, AuditLog

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

def calculate_initial_risk(is_sanctioned: bool, is_pep: bool, industry: str, country: str) -> tuple[float, str]:
    score = random.uniform(10.0, 35.0)
    
    if is_sanctioned:
        score += 50.0
    if is_pep:
        score += 30.0
    if industry in HIGH_RISK_INDUSTRIES:
        score += 15.0
    if country in HIGH_RISK_COUNTRIES:
        score += 15.0
        
    score = min(100.0, round(score, 1))
    
    if score >= 80.0:
        tier = "CRITICAL"
    elif score >= 60.0:
        tier = "HIGH"
    elif score >= 35.0:
        tier = "MEDIUM"
    else:
        tier = "LOW"
        
    return score, tier


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
        
        is_sanctioned = random.random() < 0.015  # 1.5% sanctioned
        is_pep = random.random() < 0.035         # 3.5% PEP
        
        onboarding_date = (start_date + timedelta(days=random.randint(0, 1800))).date()
        risk_score, risk_tier = calculate_initial_risk(is_sanctioned, is_pep, industry, country)

        customer = Customer(
            name=name,
            type=cust_type,
            country=country,
            industry=industry,
            expected_turnover=turnover,
            is_pep=is_pep,
            is_sanctioned=is_sanctioned,
            onboarding_date=onboarding_date,
            risk_score=risk_score,
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

    # Log audit entry for seeding
    audit_entry = AuditLog(
        entity_type="SYSTEM",
        entity_id=0,
        action="SEED_DATABASE",
        details={"seeded_customers": len(customers_to_create), "seeded_aliases": len(aliases_to_create)}
    )
    db.add(audit_entry)
    db.commit()

    print(f"[Seed] Successfully seeded {len(customers_to_create)} customers and {len(aliases_to_create)} aliases.")
