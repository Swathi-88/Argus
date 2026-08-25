"""Authentication endpoints: sign in, identity, and the role matrix."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import audit
from app.auth import (
    ALL_ROLES,
    DEMO_USERS,
    Principal,
    ROLE_PERMISSIONS,
    create_access_token,
    get_current_principal,
    verify_password,
)
from app.database import get_db
from app.models import AnalystUser
from app.schemas import LoginRequest, LoginResponse, PrincipalResponse, RoleMatrixResponse

router = APIRouter(prefix="/auth", tags=["Authentication & RBAC"])


@router.post("/login", response_model=LoginResponse, summary="Exchange credentials for a JWT")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(AnalystUser).filter(AnalystUser.username == req.username.strip()).first()

    # Same 401 for unknown user, wrong password, and disabled account — nothing
    # here should tell a caller which of the three it was.
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        audit.record(
            db,
            entity_type="AUTH",
            entity_id=user.id if user else 0,
            action="ANALYST_LOGIN_FAILED",
            actor=req.username.strip(),
            actor_role="UNKNOWN",
            details={
                "reason": "invalid_credentials_or_inactive_account",
                "client_host": request.client.host if request.client else None,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    audit.record(
        db,
        entity_type="AUTH",
        entity_id=user.id,
        action="ANALYST_LOGIN",
        actor=user.username,
        actor_role=user.role,
        details={
            "full_name": user.full_name,
            "role": user.role,
            "client_host": request.client.host if request.client else None,
            "token_expires_at": token["expires_at"],
        },
    )

    principal = Principal(
        {"sub": user.username, "uid": user.id, "name": user.full_name, "role": user.role}
    )
    return LoginResponse(**token, user=PrincipalResponse(**principal.as_dict()))


@router.get("/me", response_model=PrincipalResponse, summary="Identity and permissions of the bearer")
def whoami(principal: Principal = Depends(get_current_principal)):
    return PrincipalResponse(**principal.as_dict())


@router.get("/roles", response_model=RoleMatrixResponse, summary="The role-to-permission matrix")
def role_matrix():
    """
    Published so the console can grey out controls the signed-in role cannot use,
    rather than letting the user click and collect a 403.
    """
    return RoleMatrixResponse(
        roles={role: sorted(ROLE_PERMISSIONS.get(role, set())) for role in ALL_ROLES},
        descriptions={
            "ANALYST": "Front-line investigator. Views the queue and disposes of alerts; cannot escalate or export audit records.",
            "MANAGER": "Supervisor. Full analyst rights plus escalation to MLRO, audit export, and the ability to run the evaluation harness.",
            "AUDITOR": "Second-line assurance. Read-only across the system including the audit trail and its integrity proof; cannot act on alerts.",
        },
        demo_accounts=[
            {"username": spec["username"], "full_name": spec["full_name"], "role": spec["role"]}
            for spec in DEMO_USERS
        ],
    )
