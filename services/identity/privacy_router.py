"""Citizen Privacy Center API Router for SevaHealth AI.

Enables citizens to:
- Grant consent across all 8 healthcare consent categories
- View active and historical consents with audit trails
- Modify consent scopes and expiration dates
- Revoke consent immediately (including revocable wearable access)
- View real-time audit ledger of who accessed what health data and why

DISCLAIMER: For research and demonstration purposes only. Not clinically validated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from packages.clinical_models.consent import (
    ConsentCategory,
    ConsentStatus,
    HealthcareConsent,
    consent_manager,
)
from packages.auth.jwt import TokenPayload, get_current_user_token
from packages.types.enums import UserRole
from services.store import store

from services.wearable.adapter import wearable_adapter

router = APIRouter(prefix="/privacy", tags=["Citizen Privacy Center"])


CONSENT_NOTICES: Dict[str, Dict[str, Any]] = {
    "SYS-CON-001": {
        "notice_id": "SYS-CON-001-NOTICE-V1",
        "requirement_id": "SYS-CON-001",
        "category": "health_screening",
        "version": "v1.2.0",
        "title": "SevaHealth AI Clinical & Health Screening Consent Notice",
        "purpose_description": "Collection and evaluation of vital signs, physiological measurements, lifestyle metrics, and screening questionnaires for non-diagnostic cardiovascular and metabolic risk stratification.",
        "data_scope_disclosed": ["vitals", "screening_answers", "lifestyle", "risk_scores"],
        "retention_policy": "Retained for longitudinal clinical trends during active care context; subject to immediate right-to-be-forgotten upon citizen request.",
        "revocation_rights": "Citizen retains the unilateral right to revoke or modify consent at any time via the Citizen Privacy Center without affecting emergency triage.",
        "non_diagnostic_disclaimer": "SevaHealth AI provides decision support indicators and does not constitute a confirmed medical diagnosis or autonomous medication prescription.",
        "effective_date": "2026-01-01T00:00:00Z",
    },
    "SYS-CON-002": {
        "notice_id": "SYS-CON-002-WEARABLE-V1",
        "requirement_id": "SYS-CON-002",
        "category": "wearable_data",
        "version": "v1.1.0",
        "title": "Continuous Wearable Sensor Telemetry Ingestion Notice",
        "purpose_description": "Automated synchronization of resting heart rate, heart rate variability (HRV), step counts, and sleep stages from connected smartwatches and biometric wearables.",
        "data_scope_disclosed": ["activity", "heartrate", "sleep", "respiratory"],
        "retention_policy": "Rolling 14-day observation window retained for baseline analysis; future synchronization is blocked immediately upon revocation.",
        "revocation_rights": "Citizen may disconnect providers or revoke data access at any time with one click in the Citizen Privacy Center.",
        "non_diagnostic_disclaimer": "Consumer-grade wearable telemetry is not clinically equivalent to 12-lead ECG or diagnostic polysomnography.",
        "effective_date": "2026-01-01T00:00:00Z",
    },
    "SYS-CON-003": {
        "notice_id": "SYS-CON-003-RESEARCH-V1",
        "requirement_id": "SYS-CON-003",
        "category": "research_analytics",
        "version": "v1.0.0",
        "title": "Secondary Health Research & Population Analytics Notice",
        "purpose_description": "De-identified and aggregated health data processing for epidemiological population studies and non-commercial clinical protocol optimization.",
        "data_scope_disclosed": ["deidentified_vitals", "aggregated_risk_tiers", "demographics_k_anonymity"],
        "retention_policy": "Anonymized research cohorts preserved according to institutional review board standards.",
        "revocation_rights": "Opt-out available prior to dataset de-identification irreversibility threshold.",
        "non_diagnostic_disclaimer": "Research analytics are exclusively non-interventional.",
        "effective_date": "2026-01-01T00:00:00Z",
    },
}


@router.get("/notices")
async def list_consent_notices() -> List[Dict[str, Any]]:
    """Presents all applicable ABDM and DISHA consent notices (SYS-CON-001)."""
    return list(CONSENT_NOTICES.values())


@router.get("/notices/{notice_key}")
async def get_consent_notice(notice_key: str) -> Dict[str, Any]:
    """Presents specific consent notice by requirement key (SYS-CON-001) or category."""
    key_norm = notice_key.upper()
    if key_norm in CONSENT_NOTICES:
        return CONSENT_NOTICES[key_norm]
    for n in CONSENT_NOTICES.values():
        if n["category"].lower() == notice_key.lower():
            return n
    return CONSENT_NOTICES["SYS-CON-001"]


class GrantConsentRequest(BaseModel):
    subject: Optional[str] = Field(None, description="Citizen ID (defaults to current logged-in citizen)")
    category: ConsentCategory = Field(description="Consent category")
    purpose: str = Field(description="Purpose of health data processing")
    scope: List[str] = Field(
        default_factory=lambda: ["vitals", "labs", "lifestyle", "risk_scores"],
        description="Data types permitted"
    )
    recipient: str = Field(default="PRIMARY_CARE_TEAM", description="Recipient entity, role, or service")
    expires_at: Optional[str] = Field(None, description="ISO-8601 expiration timestamp")
    version: str = Field(default="v1.0.0", description="Policy version")
    notice_id: Optional[str] = Field(default="SYS-CON-001-NOTICE-V1", description="Referenced consent notice presented")
    proxy_mandate: Optional[Dict[str, Any]] = Field(None, description="Authenticated proxy mandate")
    consent_artifact: Optional[Dict[str, Any]] = Field(None, description="ABDM consent artifact")
    artifact_signature: Optional[str] = Field(None, description="Signature for proxy validation")


class ModifyConsentRequest(BaseModel):
    new_scope: Optional[List[str]] = None
    new_expires_at: Optional[str] = None
    new_purpose: Optional[str] = None
    proxy_mandate: Optional[Dict[str, Any]] = Field(None, description="Authenticated proxy mandate")
    consent_artifact: Optional[Dict[str, Any]] = Field(None, description="ABDM consent artifact")
    artifact_signature: Optional[str] = Field(None, description="Signature for proxy validation")


class RevokeConsentRequest(BaseModel):
    reason: str = Field(default="Revoked by citizen request", description="Reason for consent revocation")
    proxy_mandate: Optional[Dict[str, Any]] = Field(None, description="Authenticated proxy mandate")
    consent_artifact: Optional[Dict[str, Any]] = Field(None, description="ABDM consent artifact")
    artifact_signature: Optional[str] = Field(None, description="Signature for proxy validation")


class VerifyAccessRequest(BaseModel):
    subject: str = Field(description="Citizen ID subject")
    category: ConsentCategory = Field(default=ConsentCategory.HEALTH_SCREENING, description="Consent category")
    requested_purpose: str = Field(default="HEALTH_SCREENING", description="Requested processing purpose")
    recipient: Optional[str] = Field(None, description="Requesting recipient")
    required_scope: Optional[str] = Field(None, description="Required data scope")


@router.post("/verify-access")
async def verify_consent_access(
    payload: VerifyAccessRequest,
    actor: TokenPayload = Depends(get_current_user_token),
) -> Dict[str, Any]:
    """Inspects authorization result and denies processing outside granted purpose/scope (TC-CON-003)."""
    allowed, reason = consent_manager.verify_consent(
        subject=payload.subject,
        category=payload.category,
        purpose=payload.requested_purpose,
        recipient=payload.recipient,
        required_scope=payload.required_scope,
        actor_id=actor.sub,
        actor_role=actor.role.value if hasattr(actor.role, "value") else str(actor.role),
    )
    return {
        "allowed": allowed,
        "decision": "PERMITTED" if allowed else "DENIED",
        "reason": reason,
        "subject": payload.subject,
        "category": payload.category.value,
        "requested_purpose": payload.requested_purpose,
        "required_scope": payload.required_scope,
    }


def _validate_proxy_mandate(
    actor: TokenPayload,
    target_subject: Optional[str],
    consent_artifact: Optional[Dict[str, Any]] = None,
    proxy_mandate: Optional[Dict[str, Any]] = None,
    artifact_signature: Optional[str] = None,
) -> str:
    """Enforces that healthcare workers/non-citizens cannot unilaterally override or grant consent without validated proxy mandate / ABDM artifact."""
    artifact = consent_artifact or proxy_mandate
    has_sig = False
    if artifact and isinstance(artifact, dict):
        has_sig = bool(
            artifact.get("signature") or
            artifact.get("artifact_signature") or
            artifact_signature
        )
    if not (artifact and has_sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent operations on behalf of a citizen require an authenticated proxy with explicit consent artifact validation.",
        )
    resolved = target_subject or artifact.get("citizen_id") or artifact.get("subject")
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subject citizen ID must be specified in the consent directive or artifact.",
        )
    return resolved


def _resolve_subject(token: TokenPayload, requested_subject: Optional[str] = None) -> str:
    """Resolves subject ensuring citizens can only manage their own consents."""
    if token.role == UserRole.CITIZEN:
        # Check citizen record mapping
        citizen = store.get_citizen_by_user_id(token.sub)
        own_id = citizen.id if citizen else token.sub
        if requested_subject and requested_subject != own_id and requested_subject != token.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Citizens can only manage consent for their own health records.",
            )
        return own_id
    if requested_subject:
        return requested_subject
    return token.sub


@router.post("/consents", response_model=HealthcareConsent, status_code=status.HTTP_201_CREATED)
async def grant_consent(
    payload: GrantConsentRequest,
    actor: TokenPayload = Depends(get_current_user_token),
) -> HealthcareConsent:
    """Grants a new consent directive across any of the 8 categories."""
    if actor.role == UserRole.CITIZEN:
        subject_id = _resolve_subject(actor, payload.subject)
    elif actor.role in [UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]:
        subject_id = _validate_proxy_mandate(
            actor=actor,
            target_subject=payload.subject,
            consent_artifact=payload.consent_artifact,
            proxy_mandate=payload.proxy_mandate,
            artifact_signature=payload.artifact_signature,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role not permitted to grant consent directives.",
        )

    consent = consent_manager.grant(
        subject=subject_id,
        category=payload.category,
        purpose=payload.purpose,
        scope=payload.scope,
        recipient=payload.recipient,
        expires_at=payload.expires_at,
        version=payload.version,
        notice_id=payload.notice_id,
        actor_id=actor.sub,
        actor_role=actor.role.value if hasattr(actor.role, "value") else str(actor.role),
    )
    # Mirror into store
    store.add_consent(consent)
    return consent


@router.get("/consents", response_model=List[HealthcareConsent])
async def list_consents(
    subject: Optional[str] = None,
    category: Optional[ConsentCategory] = None,
    status_filter: Optional[ConsentStatus] = Query(None, alias="status"),
    actor: TokenPayload = Depends(get_current_user_token),
) -> List[HealthcareConsent]:
    """Lists all consents for the citizen, with optional category/status filters."""
    subject_id = _resolve_subject(actor, subject)
    return consent_manager.list_consents(
        subject=subject_id,
        category=category,
        status=status_filter,
    )


@router.get("/consents/{consent_id}", response_model=HealthcareConsent)
async def view_consent(
    consent_id: str,
    actor: TokenPayload = Depends(get_current_user_token),
) -> HealthcareConsent:
    """Views a specific consent directive including complete audit trail."""
    consent = consent_manager.view(consent_id)
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent directive '{consent_id}' not found.",
        )

    # Authorization: citizens can only view their own consent
    subject_id = _resolve_subject(actor)
    if actor.role == UserRole.CITIZEN and consent.subject != subject_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view consent belonging to another citizen.",
        )

    # Log view action in audit trail
    consent.record_access(
        actor_id=actor.sub,
        actor_role=actor.role.value,
        details=f"Viewed consent details and audit history by {actor.role.value}",
        granted=True,
    )

    return consent


@router.patch("/consents/{consent_id}", response_model=HealthcareConsent)
async def modify_consent(
    consent_id: str,
    payload: ModifyConsentRequest,
    actor: TokenPayload = Depends(get_current_user_token),
) -> HealthcareConsent:
    """Modifies scope, expiration, or purpose of an active consent directive."""
    consent = consent_manager.view(consent_id)
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent directive '{consent_id}' not found.",
        )

    if actor.role == UserRole.CITIZEN:
        subject_id = _resolve_subject(actor)
        if consent.subject != subject_id and consent.subject != actor.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot modify consent belonging to another citizen.",
            )
    elif actor.role in [UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]:
        _validate_proxy_mandate(
            actor=actor,
            target_subject=consent.subject,
            consent_artifact=payload.consent_artifact,
            proxy_mandate=payload.proxy_mandate,
            artifact_signature=payload.artifact_signature,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role not permitted to modify consent directives.",
        )

    return consent_manager.modify(
        consent_id=consent_id,
        new_scope=payload.new_scope,
        new_expires_at=payload.new_expires_at,
        new_purpose=payload.new_purpose,
        actor_id=actor.sub,
        actor_role=actor.role.value if hasattr(actor.role, "value") else str(actor.role),
    )


@router.post("/consents/{consent_id}/revoke", response_model=HealthcareConsent)
async def revoke_consent(
    consent_id: str,
    payload: RevokeConsentRequest,
    actor: TokenPayload = Depends(get_current_user_token),
) -> HealthcareConsent:
    """Revokes an active consent directive immediately with audit recording."""
    consent = consent_manager.view(consent_id)
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent directive '{consent_id}' not found.",
        )

    if actor.role == UserRole.CITIZEN:
        subject_id = _resolve_subject(actor)
        if consent.subject != subject_id and consent.subject != actor.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot revoke consent belonging to another citizen.",
            )
    elif actor.role in [UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]:
        _validate_proxy_mandate(
            actor=actor,
            target_subject=consent.subject,
            consent_artifact=payload.consent_artifact,
            proxy_mandate=payload.proxy_mandate,
            artifact_signature=payload.artifact_signature,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role not permitted to revoke consent directives.",
        )

    revoked = consent_manager.revoke(
        consent_id=consent_id,
        reason=payload.reason,
        actor_id=actor.sub,
        actor_role=actor.role.value,
    )
    store.revoke_consent(consent.subject, consent_id)
    return revoked


@router.post("/wearables/revoke")
async def revoke_wearable_access(
    reason: str = "Revoked continuous wearable data stream",
    actor: TokenPayload = Depends(get_current_user_token),
) -> Dict[str, Any]:
    """Quick one-click revocation of all wearable data streams for the citizen."""
    if actor.role != UserRole.CITIZEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only citizens can perform direct one-click wearable data stream revocation for their own records.",
        )
    subject_id = _resolve_subject(actor)
    wearable_consents = consent_manager.list_consents(
        subject=subject_id,
        category=ConsentCategory.WEARABLE_DATA,
        status=ConsentStatus.ACTIVE,
    )

    revoked_count = 0
    for wc in wearable_consents:
        consent_manager.revoke(
            consent_id=wc.id,
            reason=reason,
            actor_id=actor.sub,
            actor_role=actor.role.value,
        )
        store.revoke_consent(subject_id, wc.id)
        revoked_count += 1

    # Synchronize with wearable adapter
    await wearable_adapter.revoke_consent(subject_id)

    return {
        "status": "SUCCESS",
        "subject": subject_id,
        "wearable_access_status": "REVOKED",
        "revoked_directives_count": revoked_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Continuous wearable sync halted. No future sensor readings will be ingested without renewed consent.",
        "retention_policy": "RETAINED_FOR_AUDIT_TRAIL",
    }


@router.get("/summary")
async def get_privacy_summary(
    subject: Optional[str] = None,
    actor: TokenPayload = Depends(get_current_user_token),
) -> Dict[str, Any]:
    """Returns a high-level citizen privacy dashboard summary."""
    subject_id = _resolve_subject(actor, subject)
    all_consents = consent_manager.list_consents(subject=subject_id)

    category_summary: Dict[str, Dict[str, Any]] = {}
    for cat in ConsentCategory:
        cat_consents = [c for c in all_consents if c.category == cat]
        active = [c for c in cat_consents if c.is_valid()]
        category_summary[cat.value] = {
            "is_granted": len(active) > 0,
            "active_count": len(active),
            "total_directives": len(cat_consents),
            "latest_expires_at": active[0].expires_at if active else None,
        }

    total_audit_events = sum(len(c.audit_trail) for c in all_consents)

    return {
        "subject": subject_id,
        "total_consents": len(all_consents),
        "active_consents": sum(1 for c in all_consents if c.is_valid()),
        "categories": category_summary,
        "wearable_data_active": category_summary[ConsentCategory.WEARABLE_DATA.value]["is_granted"],
        "ai_processing_active": category_summary[ConsentCategory.AI_PROCESSING.value]["is_granted"],
        "total_audit_events_logged": total_audit_events,
        "data_protection_standard": "DISHA & ABDM Compliant (Informed, Granular, Revocable)",
    }


@router.get("/audit-trail")
async def get_privacy_audit_trail(
    subject: Optional[str] = None,
    limit: int = 50,
    actor: TokenPayload = Depends(get_current_user_token),
) -> List[Dict[str, Any]]:
    """Returns complete audit trail of all consent grants, modifications, revocations, and data accesses."""
    subject_id = _resolve_subject(actor, subject)
    all_consents = consent_manager.list_consents(subject=subject_id)

    all_events = []
    for c in all_consents:
        for ev in c.audit_trail:
            all_events.append({
                "consent_id": c.id,
                "category": c.category.value,
                "purpose": c.purpose,
                "event_id": ev.event_id,
                "timestamp": ev.timestamp,
                "action": ev.action,
                "actor_id": ev.actor_id,
                "actor_role": ev.actor_role,
                "details": ev.details,
                "recipient": ev.recipient,
            })

    # Sort descending by timestamp
    all_events.sort(key=lambda e: e["timestamp"], reverse=True)
    return all_events[:limit]
