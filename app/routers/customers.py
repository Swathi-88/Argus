from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Customer
from app.schemas import CustomerResponse, CustomerListResponse

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("", response_model=CustomerListResponse)
def list_customers(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier (LOW, MEDIUM, HIGH, CRITICAL)"),
    is_pep: Optional[bool] = Query(None, description="Filter by PEP status"),
    is_sanctioned: Optional[bool] = Query(None, description="Filter by Sanctions status"),
    search: Optional[str] = Query(None, description="Search term for name, country, or industry"),
    db: Session = Depends(get_db)
):
    """
    Retrieves a paginated list of customers with optional filters.
    """
    query = db.query(Customer).options(joinedload(Customer.aliases))

    if risk_tier:
        query = query.filter(Customer.risk_tier == risk_tier.upper())
    if is_pep is not None:
        query = query.filter(Customer.is_pep == is_pep)
    if is_sanctioned is not None:
        query = query.filter(Customer.is_sanctioned == is_sanctioned)
    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            (Customer.name.ilike(search_term)) |
            (Customer.country.ilike(search_term)) |
            (Customer.industry.ilike(search_term))
        )

    total = query.count()
    customers = query.order_by(Customer.id.asc()).offset((page - 1) * size).limit(size).all()

    return CustomerListResponse(
        total=total,
        page=page,
        size=size,
        customers=[CustomerResponse.model_validate(c) for c in customers]
    )


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    """
    Retrieves a single customer profile by ID, including entity aliases.
    """
    customer = db.query(Customer).options(joinedload(Customer.aliases)).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {customer_id} not found."
        )
    return CustomerResponse.model_validate(customer)
