"""
JWT authentication and role-based access control.

Three roles, matching how a real financial-crime team is split:

*   **ANALYST**  — front line. Reads the queue, investigates, and disposes of
    alerts (confirm / dismiss / request info). Cannot escalate to MLRO and
    cannot export the audit trail.
*   **MANAGER**  — supervisor. Everything an analyst can do, plus escalation,
    plus the ability to run the evaluation harness and export audit records.
*   **AUDITOR**  — second line / assurance. Read-only across the whole system,
    including the audit trail and its integrity proof, but cannot touch an
    alert. Independence is the point: an auditor who can act on the alerts they
    review is not an auditor.

Passwords use PBKDF2-HMAC-SHA256 from the standard library. That is a deliberate
choice over bcrypt/argon2 here: it avoids a native build dependency for a
prototype, is FIPS-approved, and at 260k iterations is appropriate for demo
accounts. A production deployment should move to Argon2id and an external IdP.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import audit
from app.config import settings
from app.database import get_db
from app.models import AnalystUser

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Roles and permissions
# --------------------------------------------------------------------------

ROLE_ANALYST = "ANALYST"
ROLE_MANAGER = "MANAGER"
ROLE_AUDITOR = "AUDITOR"
ALL_ROLES = (ROLE_ANALYST, ROLE_MANAGER, ROLE_AUDITOR)

# Permission verbs, kept coarse enough to reason about and fine enough to gate on.
P_VIEW_ALERTS = "alerts:view"
P_ACT_ALERTS = "alerts:act"            # confirm / dismiss / request info
P_ESCALATE_ALERTS = "alerts:escalate"  # escalate to MLRO — supervisory
P_VIEW_CUSTOMERS = "customers:view"
P_INGEST_EVENTS = "events:ingest"
P_VIEW_AUDIT = "audit:view"
P_EXPORT_AUDIT = "audit:export"
P_VERIFY_AUDIT = "audit:verify"
P_VIEW_EVALUATION = "evaluation:view"
P_RUN_EVALUATION = "evaluation:run"

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_ANALYST: {
        P_VIEW_ALERTS,
        P_ACT_ALERTS,
        P_VIEW_CUSTOMERS,
        P_INGEST_EVENTS,
        P_VIEW_AUDIT,
        P_VIEW_EVALUATION,
    },
    ROLE_MANAGER: {
        P_VIEW_ALERTS,
        P_ACT_ALERTS,
        P_ESCALATE_ALERTS,
        P_VIEW_CUSTOMERS,
        P_INGEST_EVENTS,
        P_VIEW_AUDIT,
        P_EXPORT_AUDIT,
        P_VERIFY_AUDIT,
        P_VIEW_EVALUATION,
        P_RUN_EVALUATION,
    },
    ROLE_AUDITOR: {
        # Read everything, act on nothing.
        P_VIEW_ALERTS,
        P_VIEW_CUSTOMERS,
        P_VIEW_AUDIT,
        P_EXPORT_AUDIT,
        P_VERIFY_AUDIT,
        P_VIEW_EVALUATION,
    },
}

# Which analyst dispositions each permission covers.
ACTION_PERMISSION = {
    "CONFIRMED": P_ACT_ALERTS,
    "DISMISSED": P_ACT_ALERTS,
    "INFO_REQUESTED": P_ACT_ALERTS,
    "ESCALATED": P_ESCALATE_ALERTS,
}


def permissions_for(role: str) -> Set[str]:
    return ROLE_PERMISSIONS.get((role or "").upper(), set())


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
        )
        # Constant-time, so a wrong password cannot be found byte by byte.
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


# --------------------------------------------------------------------------
# JWT — HS256, encoded here to avoid another dependency
# --------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(message: bytes) -> bytes:
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), message, hashlib.sha256).digest()


def create_access_token(user: AnalystUser) -> Dict[str, Any]:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.JWT_EXPIRY_MINUTES)

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user.username,
        "uid": user.id,
        "name": user.full_name,
        "role": user.role.upper(),
        "perms": sorted(permissions_for(user.role)),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "dynamic-risk-trigger-engine",
    }

    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    segments.append(_b64url_encode(_sign(signing_input)))

    return {
        "access_token": ".".join(segments),
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "expires_in": settings.JWT_EXPIRY_MINUTES * 60,
    }


def decode_access_token(token: str) -> Dict[str, Any]:
    """Verifies signature and expiry. Raises ValueError on any problem."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise ValueError("Malformed token")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = _sign(signing_input)
    if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
        raise ValueError("Signature verification failed")

    payload = json.loads(_b64url_decode(payload_b64))

    if payload.get("exp", 0) < int(datetime.now(timezone.utc).timestamp()):
        raise ValueError("Token has expired")

    return payload


# --------------------------------------------------------------------------
# FastAPI dependencies
# --------------------------------------------------------------------------

# auto_error=False so a missing header produces our own 401 with a useful body
# rather than the default terse one.
_bearer = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")


class Principal:
    """The authenticated caller, as the request handlers see them."""

    def __init__(self, payload: Dict[str, Any]):
        self.username: str = payload.get("sub", "unknown")
        self.user_id: Optional[int] = payload.get("uid")
        self.full_name: str = payload.get("name", self.username)
        self.role: str = (payload.get("role") or "").upper()
        # Recomputed from the role rather than trusted from the token body, so
        # a permission change takes effect without waiting for token expiry.
        self.permissions: Set[str] = permissions_for(self.role)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def as_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "user_id": self.user_id,
            "full_name": self.full_name,
            "role": self.role,
            "permissions": sorted(self.permissions),
        }


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. POST /auth/login to obtain a bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(payload)


def require(permission: str):
    """
    Dependency factory gating an endpoint on a single permission.

    A refused request is itself auditable — a denied attempt is exactly the kind
    of thing an assurance function wants to see — so it writes a
    PERMISSION_DENIED record before raising.

        @router.post("/x", dependencies=[Depends(require(P_ACT_ALERTS))])
    """

    def dependency(
        request: Request,
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> Principal:
        if not principal.can(permission):
            try:
                audit.record(
                    db,
                    entity_type="AUTH",
                    entity_id=principal.user_id or 0,
                    action="PERMISSION_DENIED",
                    actor=principal.username,
                    actor_role=principal.role,
                    details={
                        "required_permission": permission,
                        "role": principal.role,
                        "path": str(request.url.path),
                        "method": request.method,
                    },
                )
            except Exception as exc:  # never let audit failure mask the 403
                logger.warning("Could not audit permission denial: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {principal.role} lacks the '{permission}' permission. "
                    f"Granted: {', '.join(sorted(principal.permissions)) or 'none'}."
                ),
            )
        return principal

    return dependency


# --------------------------------------------------------------------------
# Demo account provisioning
# --------------------------------------------------------------------------

# One account per role so every gate in the UI can be demonstrated. Passwords
# come from the environment when set; the documented defaults are for local
# demo use only and are printed at startup so nothing is hidden.
DEMO_USERS = [
    {
        "username": "a.chen",
        "full_name": "Amara Chen",
        "email": "a.chen@example-bank.test",
        "role": ROLE_ANALYST,
        "env_var": "DEMO_ANALYST_PASSWORD",
        "default_password": "analyst123",
    },
    {
        "username": "r.okafor",
        "full_name": "Rem Okafor",
        "email": "r.okafor@example-bank.test",
        "role": ROLE_MANAGER,
        "env_var": "DEMO_MANAGER_PASSWORD",
        "default_password": "manager123",
    },
    {
        "username": "j.lindqvist",
        "full_name": "Jo Lindqvist",
        "email": "j.lindqvist@example-bank.test",
        "role": ROLE_AUDITOR,
        "env_var": "DEMO_AUDITOR_PASSWORD",
        "default_password": "auditor123",
    },
]


def seed_demo_users(db: Session) -> int:
    """Creates the demo accounts if absent. Never overwrites an existing one."""
    created = 0
    for spec in DEMO_USERS:
        existing = db.query(AnalystUser).filter(AnalystUser.username == spec["username"]).first()
        if existing:
            continue
        password = os.getenv(spec["env_var"], spec["default_password"])
        db.add(
            AnalystUser(
                username=spec["username"],
                full_name=spec["full_name"],
                email=spec["email"],
                role=spec["role"],
                password_hash=hash_password(password),
                is_active=True,
            )
        )
        created += 1
    if created:
        db.commit()
        print(f"[Auth] Provisioned {created} demo account(s):")
        for spec in DEMO_USERS:
            shown = os.getenv(spec["env_var"], spec["default_password"])
            print(f"        {spec['role']:<8} {spec['username']:<14} / {shown}")
    return created
