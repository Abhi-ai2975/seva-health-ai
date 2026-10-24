from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from packages.config.settings import settings
from packages.types.enums import UserRole

import hashlib
import bcrypt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


import uuid


class TokenPayload(BaseModel):
    sub: str
    tenant_id: str
    role: UserRole
    scopes: List[str] = []
    jti: Optional[str] = None
    exp: Optional[int] = None


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """Ensures secure passwords in compliance with healthcare data security requirements."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one numeric digit."
    return True, ""


def is_bcrypt_hash(hashed_password: str) -> bool:
    """Checks whether a hash string is a valid bcrypt hash format ($2b$ or $2a$)."""
    return bool(hashed_password and (hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$")))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a stored hash using bcrypt exclusively.
    Plaintext password comparisons are strictly disallowed.
    Supports legacy unsalted single-iteration SHA-256 strictly for backward compatibility
    to enable automatic migration to bcrypt on successful login.
    """
    if not plain_password or not hashed_password:
        return False

    if is_bcrypt_hash(hashed_password):
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
        except Exception:
            return False

    # Transitional check for legacy unsalted SHA-256 (64 hex characters) to enable seamless migration
    if len(hashed_password) == 64 and all(c in "0123456789abcdefABCDEF" for c in hashed_password):
        import hmac
        computed_sha = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(computed_sha.lower(), hashed_password.lower())

    # Plaintext comparison (plain_password == hashed_password) is strictly disallowed
    return False


def get_password_hash(password: str) -> str:
    """Hashes a password using bcrypt with a salt exclusively."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")



# Role-to-Scope Least-Privilege Mapping
ROLE_SCOPES: Dict[UserRole, List[str]] = {
    UserRole.CITIZEN: [
        "citizen:read_self",
        "consent:manage",
        "observations:read_self",
        "intervention:read_self",
        "documents:read_self",
    ],
    UserRole.HEALTH_WORKER: [
        "screening:create",
        "screening:read",
        "citizen:read",
        "intervention:read",
        "intervention:write",
        "observations:create",
        "observations:read",
    ],
    UserRole.CLINICIAN: [
        "clinical:read",
        "clinical:write",
        "screening:read",
        "screening:review",
        "risk:read",
        "risk:create",
        "careplan:read",
        "careplan:write",
        "intervention:read",
        "intervention:write",
        "documents:read",
        "documents:write",
        "referral:manage",
        "triage:manage",
    ],
    UserRole.PUBLIC_HEALTH_ADMIN: [
        "population:read",
        "analytics:read",
        "reports:read",
    ],
    UserRole.SYSTEM_ADMIN: ["*"],
}


def get_scopes_for_role(role: UserRole) -> List[str]:
    """Returns least-privilege authorized scopes according to authenticated role."""
    return list(ROLE_SCOPES.get(role, ["citizen:read_self"]))


def create_access_token(
    subject: str,
    tenant_id: str,
    role: UserRole,
    scopes: Optional[List[str]] = None,
    expires_delta: Optional[timedelta] = None,
    jti: Optional[str] = None
) -> str:
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    if scopes is None:
        effective_scopes = get_scopes_for_role(role)
    else:
        effective_scopes = list(scopes)

    token_jti = jti or str(uuid.uuid4())
    to_encode = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role.value,
        "scopes": effective_scopes,
        "jti": token_jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: str,
    tenant_id: str,
    role: UserRole,
    jti: Optional[str] = None
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    token_jti = jti or str(uuid.uuid4())
    to_encode = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role.value,
        "type": "refresh",
        "jti": token_jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> TokenPayload:
    from services.store import store
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        jti = payload.get("jti")
        if jti and store.is_token_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked / logged out",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenPayload(
            sub=payload["sub"],
            tenant_id=payload.get("tenant_id", "default_org"),
            role=UserRole(payload.get("role", UserRole.CITIZEN.value)),
            scopes=payload.get("scopes", []),
            jti=jti,
            exp=payload.get("exp"),
        )
    except JWTError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err


async def get_current_user_token(
    token: Optional[str] = Depends(oauth2_scheme)
) -> TokenPayload:
    """Dependency for authenticated routes requiring valid Bearer token."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(token)


async def get_current_user_token_optional(
    token: Optional[str] = Depends(oauth2_scheme)
) -> TokenPayload:
    """Bearer token dependency that strictly restricts mock fallback to development environments.
    Never allows unauthenticated fallback in production, and never swallows invalid token errors.
    If a Bearer token is provided in the Authorization header, it is decoded and validated.
    """
    if token:
        return decode_access_token(token)

    if settings.ENVIRONMENT == "development":
        return TokenPayload(
            sub="worker-asha-01",
            tenant_id="tenant-karnataka-moh",
            role=UserRole.HEALTH_WORKER,
            scopes=["screening:create", "screening:read", "citizen:read", "intervention:read", "intervention:write"]
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication token required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_roles(allowed_roles: List[UserRole]):
    """Role-based authorization guard factory (adapted from bezs-iam)."""
    async def role_checker(
        current_token: TokenPayload = Depends(get_current_user_token)
    ) -> TokenPayload:
        if current_token.role not in allowed_roles and current_token.role != UserRole.SYSTEM_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted for role {current_token.role.value}"
            )
        return current_token
    return role_checker
