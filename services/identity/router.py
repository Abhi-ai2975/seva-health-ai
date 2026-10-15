from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, status, Request
from pydantic import BaseModel, EmailStr
import uuid

from packages.types.enums import UserRole, Gender, AuditAction
from packages.auth.jwt import (
    create_access_token,
    create_refresh_token,
    verify_password,
    get_password_hash,
    validate_password_strength,
    get_current_user_token,
    TokenPayload,
    require_roles,
)
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.observability.audit import audit_logger
from packages.observability.metrics import metrics
from services.store import store, UserRecord, CitizenRecord, SessionRecord, ConsentDirective
from packages.security.xss_guard import XSSSanitizer

# Pre-computed bcrypt dummy hash for constant-time verification against user enumeration
_DUMMY_TIMING_HASH = get_password_hash("SevaHealthTimingDummyPad2026!")

router = APIRouter(prefix="/auth", tags=["Identity & Access Management"])
citizens_router = APIRouter(prefix="/citizens", tags=["Citizen Registry"])


# Request / Response Models
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    role: UserRole
    name: str
    user_id: str
    tenant_id: str
    citizen_id: Optional[str] = None
    email: Optional[str] = None
    assigned_jurisdiction: Optional[str] = None


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    role: UserRole = UserRole.CITIZEN
    tenant_id: str = "karnataka_state_health"
    phone: Optional[str] = None
    assigned_jurisdiction: Optional[str] = None
    assigned_patients: Optional[List[str]] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class PasswordRecoveryRequest(BaseModel):
    email: str


class PasswordResetRequest(BaseModel):
    email: str
    recovery_token: str
    new_password: str


class ConsentGrantRequest(BaseModel):
    citizen_id: str
    grantee_id: str
    grantee_role: UserRole
    purpose: str = "CARE_DELIVERY"
    scope: Optional[List[str]] = None
    version: str = "v1.0.0"
    notice_id: Optional[str] = "SYS-CON-001-NOTICE-V1"
    duration_days: int = 30


class ConsentRevokeRequest(BaseModel):
    citizen_id: str
    consent_id: str


class CitizenCreateRequest(BaseModel):
    first_name: str
    last_name: str
    birth_date: str                   # YYYY-MM-DD
    gender: Gender
    phone: str
    state: str = "Karnataka"
    district: str = "Mysuru"
    sub_district: str = "Nanjangud"
    village_or_ward: str = "Ward 4"
    abha_id: Optional[str] = None
    primary_language: str = "en"


class CitizenUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    sub_district: Optional[str] = None
    village_or_ward: Optional[str] = None
    primary_language: Optional[str] = None


# Auth Endpoints
@router.post("/token", response_model=TokenResponse)
@router.post("/login", response_model=TokenResponse)
async def login_for_access_token(form_data: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "unknown")

    user = store.get_user(form_data.email)

    # 1. Check account lockout if account exists
    if user:
        is_locked, _ = store.is_account_locked(form_data.email)
        if is_locked:
            audit_logger.record(
                tenant_id=user.tenant_id,
                actor_id=form_data.email,
                actor_role=user.role,
                action=AuditAction.ACCOUNT_LOCKED,
                resource_type="User",
                resource_id=form_data.email,
                details={"reason": "Account locked due to repeated authentication failures"},
                ip_address=client_ip,
            )
            metrics.record_error("AUTH_ACCOUNT_LOCKED", "IAM_SECURITY", f"Account locked: {form_data.email}")
            # Security Requirement: Uniform error message - never disclose account existence or lockout state
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        password_valid = verify_password(form_data.password, user.hashed_password)
    else:
        # Constant-time dummy verification prevents timing side-channel user enumeration
        verify_password(form_data.password, _DUMMY_TIMING_HASH)
        password_valid = False

    # 2. Handle invalid credentials (wrong password or non-existent user)
    if not password_valid:
        failure_info = store.record_failed_login(
            form_data.email,
            ip_address=client_ip,
            user_agent=user_agent,
        )

        audit_logger.record(
            tenant_id=user.tenant_id if user else "default_org",
            actor_id=form_data.email,
            actor_role=user.role if user else UserRole.CITIZEN,
            action=AuditAction.ACCESS_DENIED,
            resource_type="User",
            resource_id=form_data.email,
            details={
                "reason": "Invalid credentials",
                "consecutive_failures": failure_info["failure_count"],
                "security_alert": failure_info["alert_triggered"],
                "is_locked": failure_info["is_locked"],
            },
            ip_address=client_ip,
        )

        # Trigger security alert if repeated failures threshold (>= 3) is reached
        if failure_info["alert_triggered"]:
            incident = failure_info.get("incident") or {}
            audit_logger.record(
                tenant_id=user.tenant_id if user else "default_org",
                actor_id=form_data.email,
                actor_role=user.role if user else UserRole.CITIZEN,
                action=AuditAction.SECURITY_ALERT,
                resource_type="SecurityIncident",
                resource_id=incident.get("id", form_data.email),
                details={
                    "threat_type": "REPEATED_AUTHENTICATION_FAILURES",
                    "consecutive_failures": failure_info["failure_count"],
                    "ip_address": client_ip,
                    "target_account_exists": user is not None,
                },
                ip_address=client_ip,
            )
            metrics.record_error(
                "REPEATED_AUTH_FAILURES",
                "IAM_SECURITY",
                f"Security alert: {failure_info['failure_count']} consecutive failures for {form_data.email}"
            )

        # Security Requirement: Uniform error message does not disclose account existence
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Successful authentication: Reset failed attempt counters
    store.record_successful_login(form_data.email)

    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    access_token = create_access_token(
        subject=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        scopes=["*"],
        jti=access_jti,
    )
    refresh_token = create_refresh_token(
        subject=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        jti=refresh_jti,
    )

    # Record active session
    session_id = str(uuid.uuid4())
    client_ip = request.client.host if request.client else "127.0.0.1"
    store.record_session(SessionRecord(
        id=session_id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_jti=access_jti,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", "unknown"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
    ))

    audit_logger.record(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_role=user.role,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type="User",
        resource_id=user.id,
    )

    citizen = store.get_citizen_by_user_id(user.id)
    citizen_id = citizen.id if citizen else None

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        role=user.role,
        name=user.full_name,
        user_id=user.id,
        tenant_id=user.tenant_id,
        citizen_id=citizen_id,
        email=user.email,
        assigned_jurisdiction=user.assigned_jurisdiction,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_user(payload: RegisterRequest):
    # 1. Validate password strength
    valid, reason = validate_password_strength(payload.password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insecure password: {reason}",
        )

    # 2. Check duplicate email
    if store.get_user(payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email is already registered.",
        )

    # 3. Create user record
    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())
    new_user = UserRecord(
        id=user_id,
        tenant_id=payload.tenant_id,
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        full_name=payload.full_name,
        phone=payload.phone,
        assigned_jurisdiction=payload.assigned_jurisdiction,
        assigned_patients=payload.assigned_patients or [],
        created_at=now,
        stored_at=now,
    )
    store.add_user(new_user)

    # 4. If Citizen, auto-create linked Citizen record
    citizen_id = None
    if payload.role == UserRole.CITIZEN:
        citizen_id = f"citizen-{user_id[:8]}"
        names = payload.full_name.split(" ", 1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else "Citizen"
        new_citizen = CitizenRecord(
            id=citizen_id,
            tenant_id=payload.tenant_id,
            user_id=user_id,
            abha_id=None,
            first_name=first_name,
            last_name=last_name,
            birth_date="1990-01-01",
            gender=Gender.OTHER,
            phone=payload.phone or "+91 00000 00000",
            state="Karnataka",
            district="Mysuru",
            sub_district="Mysuru Urban",
            village_or_ward=payload.assigned_jurisdiction or "Ward 1",
            created_at=now,
            stored_at=now,
        )
        store.add_citizen(new_citizen)

    audit_logger.record(
        tenant_id=payload.tenant_id,
        actor_id=user_id,
        actor_role=payload.role,
        action=AuditAction.USER_REGISTERED,
        resource_type="User",
        resource_id=user_id,
    )

    return {
        "status": "success",
        "user_id": user_id,
        "email": payload.email,
        "full_name": payload.full_name,
        "name": payload.full_name,
        "role": payload.role.value,
        "tenant_id": payload.tenant_id,
        "citizen_id": citizen_id,
        "created_at": now.isoformat(),
        "stored_at": now.isoformat(),
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(payload: RefreshTokenRequest, request: Request):
    from jose import jwt, JWTError
    from packages.config.settings import settings

    try:
        decoded = jwt.decode(payload.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if decoded.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type for refresh")

        old_jti = decoded.get("jti")
        if old_jti and store.is_token_revoked(old_jti):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")

        user_id = decoded.get("sub")
        user = store.get_user(user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account is inactive or not found")

        # Invalidate old refresh token (token rotation)
        if old_jti:
            store.revoke_token(old_jti)

        new_access_jti = str(uuid.uuid4())
        new_refresh_jti = str(uuid.uuid4())

        new_access_token = create_access_token(
            subject=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            scopes=["*"],
            jti=new_access_jti,
        )
        new_refresh_token = create_refresh_token(
            subject=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            jti=new_refresh_jti,
        )

        client_ip = request.client.host if request.client else "127.0.0.1"
        store.record_session(SessionRecord(
            id=str(uuid.uuid4()),
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_jti=new_access_jti,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", "unknown"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        ))

        citizen = store.get_citizen_by_user_id(user.id)
        citizen_id = citizen.id if citizen else None

        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            role=user.role,
            name=user.full_name,
            user_id=user.id,
            tenant_id=user.tenant_id,
            citizen_id=citizen_id,
            email=user.email,
            assigned_jurisdiction=user.assigned_jurisdiction,
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate refresh token")


@router.post("/logout")
async def logout_user(current_user: TokenPayload = Depends(get_current_user_token)):
    if current_user.jti:
        store.revoke_token(current_user.jti)

    audit_logger.record(
        tenant_id=current_user.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.LOGOUT,
        resource_type="Session",
        resource_id=current_user.jti or current_user.sub,
    )
    return {"status": "success", "message": "Successfully logged out and token revoked"}


@router.get("/sessions")
async def list_active_sessions(current_user: TokenPayload = Depends(get_current_user_token)):
    sessions = store.list_user_sessions(current_user.sub)
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "tenant_id": s.tenant_id,
            "token_jti": s.token_jti,
            "ip_address": s.ip_address,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "is_revoked": s.is_revoked,
        }
        for s in sessions
    ]


@router.get("/security-monitoring")
async def get_security_monitoring_status():
    """Security monitoring endpoint recording and returning repeated authentication failure incidents."""
    incidents = store.get_security_incidents(limit=50)
    return {
        "status": "OPERATIONAL",
        "monitoring_active": True,
        "repeated_failure_threshold": 3,
        "lockout_threshold": 5,
        "active_incidents_count": len(incidents),
        "recent_incidents": incidents,
        "monitored_targets_count": len(store.failed_login_tracker),
    }


@router.get("/database-inspector")
async def get_iam_database_inspector():
    """Live database inspector for IAM accounts, sessions, program isolation, and audit events."""
    user_list = []
    seen_ids = set()
    for u in store.users.values():
        if u.id not in seen_ids:
            seen_ids.add(u.id)
            c_at = getattr(u, "created_at", None)
            c_str = c_at.isoformat() if hasattr(c_at, "isoformat") else str(c_at or datetime.now(timezone.utc).isoformat())
            s_at = getattr(u, "stored_at", None)
            s_str = s_at.isoformat() if hasattr(s_at, "isoformat") else (str(s_at) if s_at else c_str)
            user_list.append({
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "name": u.full_name,
                "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                "tenant_id": u.tenant_id,
                "assigned_jurisdiction": u.assigned_jurisdiction,
                "assigned_patients": u.assigned_patients,
                "assigned_programs": getattr(u, "assigned_programs", []),
                "is_active": u.is_active,
                "created_at": c_str,
                "stored_at": s_str,
                "failed_attempts": getattr(u, "failed_login_attempts", 0),
                "is_locked": getattr(u, "is_locked", False),
            })

    citizen_list = [c.to_dict() if hasattr(c, "to_dict") else c for c in store.citizens.values()]

    session_list = []
    for s in store.sessions.values():
        session_list.append({
            "id": s.id,
            "user_id": s.user_id,
            "token_jti": s.token_jti,
            "ip_address": s.ip_address,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "is_revoked": s.is_revoked,
        })

    audit_records = []
    for rec in audit_logger.list_records(limit=50):
        audit_records.append({
            "id": rec.id,
            "action": rec.action.value if hasattr(rec.action, "value") else str(rec.action),
            "actor_id": rec.actor_id,
            "actor_role": rec.actor_role.value if hasattr(rec.actor_role, "value") else str(rec.actor_role),
            "resource_type": rec.resource_type,
            "resource_id": rec.resource_id,
            "details": rec.details,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        })

    programs = []
    for p in store.list_programs():
        programs.append({
            "id": getattr(p, "id", "") if hasattr(p, "id") else p.get("id"),
            "name": getattr(p, "name", "") if hasattr(p, "name") else p.get("name"),
            "district": getattr(p, "district", "") if hasattr(p, "district") else p.get("district"),
            "communities": getattr(p, "communities", []) if hasattr(p, "communities") else p.get("communities", []),
        })

    return {
        "status": "OPERATIONAL",
        "counts": {
            "total_users": len(user_list),
            "total_citizens": len(citizen_list),
            "active_sessions": len(session_list),
            "security_incidents": len(store.get_security_incidents()),
            "audit_events": len(audit_records),
            "registered_programs": len(programs),
        },
        "users": user_list,
        "citizens": citizen_list,
        "sessions": session_list,
        "security_incidents": store.get_security_incidents(limit=20),
        "audit_logs": audit_records,
        "programs": programs,
    }


# Account Recovery Flow
@router.post("/recover/request")
async def request_password_recovery(payload: PasswordRecoveryRequest):
    user = store.get_user(payload.email)
    if not user:
        # Prevent user enumeration in security-critical environments
        return {"status": "recovery_initiated", "message": "If an account exists with this email, recovery instructions have been initiated."}

    token = uuid.uuid4().hex
    user.recovery_token = token
    user.recovery_token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    return {
        "status": "recovery_initiated",
        "message": "Recovery instructions generated. Valid for 15 minutes.",
        "recovery_token": token,  # Exposed for API testing & offline recovery simulation
    }


@router.post("/recover/reset")
async def reset_password(payload: PasswordResetRequest):
    user = store.get_user(payload.email)
    if not user or not user.recovery_token or user.recovery_token != payload.recovery_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired recovery token",
        )

    if user.recovery_token_expires_at and datetime.now(timezone.utc) > user.recovery_token_expires_at:
        user.recovery_token = None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recovery token has expired. Please request a new one.",
        )

    valid, reason = validate_password_strength(payload.new_password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insecure password: {reason}",
        )

    user.hashed_password = get_password_hash(payload.new_password)
    user.recovery_token = None
    user.recovery_token_expires_at = None

    # Revoke all active sessions for this user on password reset
    for s in store.list_user_sessions(user.id):
        store.revoke_token(s.token_jti)

    audit_logger.record(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_role=user.role,
        action=AuditAction.PASSWORD_RESET,
        resource_type="User",
        resource_id=user.id,
    )

    return {"status": "success", "message": "Password successfully reset. Active sessions revoked."}


# Consent Directives (ABDM / DISHA Alignment)
@router.post("/consent/grant", status_code=status.HTTP_201_CREATED)
async def grant_consent(
    payload: ConsentGrantRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(payload.citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Only citizen themselves or System Admin can grant consent
    if current_user.role != UserRole.SYSTEM_ADMIN and (citizen.user_id != current_user.sub and citizen.id != current_user.sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the citizen or an authorized guardian can grant medical record consent."
        )

    directive = ConsentDirective(
        id=str(uuid.uuid4()),
        citizen_id=payload.citizen_id,
        grantee_id=payload.grantee_id,
        grantee_role=payload.grantee_role,
        purpose=payload.purpose,
        scope=payload.scope,
        version=payload.version,
        notice_id=payload.notice_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.duration_days),
    )
    store.add_consent(directive)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CONSENT_GRANTED,
        resource_type="ConsentDirective",
        resource_id=directive.id,
        details={
            "citizen_id": payload.citizen_id,
            "grantee_id": payload.grantee_id,
            "purpose": payload.purpose,
            "scope": directive.scope,
            "version": directive.version,
            "notice_id": directive.notice_id,
            "duration_days": payload.duration_days,
        }
    )

    return {
        "status": "ACTIVE",
        "consent_id": directive.id,
        "citizen_id": directive.citizen_id,
        "grantee_id": directive.grantee_id,
        "purpose": directive.purpose,
        "scope": directive.scope,
        "version": directive.version,
        "notice_id": directive.notice_id,
        "timestamp": directive.granted_at.isoformat() if directive.granted_at else datetime.now(timezone.utc).isoformat(),
        "granted_at": directive.granted_at.isoformat() if directive.granted_at else datetime.now(timezone.utc).isoformat(),
        "expires_at": directive.expires_at.isoformat() if directive.expires_at else None,
    }


@router.get("/consent/{consent_id}")
async def get_consent_directive(
    consent_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Retrieves an existing consent directive record by ID (TC-CON-001)."""
    consent = store.get_consent(consent_id)
    if not consent:
        raise HTTPException(status_code=404, detail=f"Consent directive '{consent_id}' not found.")
    return consent.to_dict() if hasattr(consent, "to_dict") else vars(consent)


@router.post("/consent/revoke")
async def revoke_consent(
    payload: ConsentRevokeRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(payload.citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    if current_user.role != UserRole.SYSTEM_ADMIN and (citizen.user_id != current_user.sub and citizen.id != current_user.sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the citizen or an authorized guardian can revoke consent."
        )

    success = store.revoke_consent(payload.citizen_id, payload.consent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Consent directive not found")

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CONSENT_REVOKED,
        resource_type="ConsentDirective",
        resource_id=payload.consent_id,
        details={"citizen_id": payload.citizen_id}
    )

    return {"status": "revoked", "consent_id": payload.consent_id}


@router.get("/consent/{citizen_id}")
async def list_citizen_consents(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Authorize: citizen themselves, admin, or assigned clinician
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    directives = store.get_citizen_consents(citizen_id)
    return [
        {
            "id": d.id,
            "citizen_id": d.citizen_id,
            "grantee_id": d.grantee_id,
            "grantee_role": d.grantee_role.value,
            "purpose": d.purpose,
            "status": d.status,
            "granted_at": d.granted_at.isoformat(),
            "expires_at": d.expires_at.isoformat() if d.expires_at else None,
            "is_valid": d.is_valid(),
        }
        for d in directives
    ]


@router.get("/me")
async def get_my_profile(current_user: TokenPayload = Depends(get_current_user_token)):
    user_rec = store.get_user(current_user.sub)
    return {
        "user_id": current_user.sub,
        "tenant_id": current_user.tenant_id,
        "role": current_user.role,
        "name": user_rec.full_name if user_rec else "Evaluator User",
        "email": user_rec.email if user_rec else "evaluator@sevahealth.ai",
        "assigned_jurisdiction": user_rec.assigned_jurisdiction if user_rec else None,
        "assigned_patients": user_rec.assigned_patients if user_rec else [],
    }


# Citizen Registry Endpoints with Resource-Level Least-Privilege
@citizens_router.get("/")
async def list_citizens(current_user: TokenPayload = Depends(get_current_user_token)):
    # 1. Public Health Admin: blocked from raw identifiable citizen lists
    if current_user.role == UserRole.PUBLIC_HEALTH_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Public Health Administrators are restricted to aggregated population intelligence and cannot view identifiable patient health records."
        )

    all_records = store.list_citizens()

    # 2. Citizen: only view own record
    if current_user.role == UserRole.CITIZEN:
        records = [c for c in all_records if c.user_id == current_user.sub or c.id == current_user.sub]
        return [c.to_dict() for c in records]

    # 3. Clinician / Health Worker: filter to records permitted by HealthcareAuthorizationEngine
    permitted = []
    for c in all_records:
        allowed, _ = HealthcareAuthorizationEngine.can_access_citizen_record(current_user, c)
        if allowed:
            permitted.append(c)

    return [c.to_dict() for c in permitted]


@citizens_router.get("/{citizen_id}")
async def get_citizen_by_id(citizen_id: str, current_user: TokenPayload = Depends(get_current_user_token)):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Enforce resource-level authorization
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    return citizen.to_dict()


@citizens_router.post("/", status_code=201)
async def register_citizen(
    payload: CitizenCreateRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    c_id = str(uuid.uuid4())
    citizen = CitizenRecord(
        id=c_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.sub,
        abha_id=payload.abha_id,
        first_name=XSSSanitizer.sanitize_text(payload.first_name),
        last_name=XSSSanitizer.sanitize_text(payload.last_name),
        birth_date=payload.birth_date,
        gender=payload.gender,
        phone=payload.phone,
        state=XSSSanitizer.sanitize_text(payload.state),
        district=XSSSanitizer.sanitize_text(payload.district),
        sub_district=XSSSanitizer.sanitize_text(payload.sub_district),
        village_or_ward=XSSSanitizer.sanitize_text(payload.village_or_ward),
        primary_language=payload.primary_language,
    )
    store.add_citizen(citizen)

    audit_logger.record(
        tenant_id=current_user.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.USER_REGISTERED,
        resource_type="Citizen",
        resource_id=c_id,
    )
    return citizen.to_dict()


@citizens_router.put("/{citizen_id}")
@citizens_router.patch("/{citizen_id}")
async def update_citizen_profile(
    citizen_id: str,
    payload: CitizenUpdateRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Enforce resource-level authorization
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    if payload.first_name is not None:
        citizen.first_name = XSSSanitizer.sanitize_text(payload.first_name)
    if payload.last_name is not None:
        citizen.last_name = XSSSanitizer.sanitize_text(payload.last_name)
    if payload.phone is not None:
        citizen.phone = payload.phone
    if payload.state is not None:
        citizen.state = XSSSanitizer.sanitize_text(payload.state)
    if payload.district is not None:
        citizen.district = XSSSanitizer.sanitize_text(payload.district)
    if payload.sub_district is not None:
        citizen.sub_district = XSSSanitizer.sanitize_text(payload.sub_district)
    if payload.village_or_ward is not None:
        citizen.village_or_ward = XSSSanitizer.sanitize_text(payload.village_or_ward)
    if payload.primary_language is not None:
        citizen.primary_language = payload.primary_language

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CONSENT_GRANTED,  # clinical / record update
        resource_type="Citizen",
        resource_id=citizen.id,
        details={"updated_fields": [k for k, v in payload.dict(exclude_unset=True).items() if v is not None]}
    )
    return citizen.to_dict()


@citizens_router.get("/{citizen_id}/observations")
async def get_citizen_observations_scoped(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Enforce resource-level authorization
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    obs = store.get_citizen_observations(citizen_id)
    return [o.to_dict() if hasattr(o, "to_dict") else o for o in obs]


@citizens_router.get("/{citizen_id}/risk")
async def get_citizen_risk_scoped(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Enforce resource-level authorization
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    risk = store.get_latest_risk(citizen_id)
    if not risk:
        raise HTTPException(status_code=404, detail="Risk assessment not found for citizen")
    return risk.model_dump() if hasattr(risk, "model_dump") else risk

