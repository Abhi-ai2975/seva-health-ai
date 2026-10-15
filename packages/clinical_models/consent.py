"""Healthcare Consent and Privacy Management for SevaHealth AI.

Supports 8 consent categories:
- clinical care
- health screening
- wearable data
- AI processing
- research/analytics
- notifications
- data sharing
- caregiver/family access

Every consent contains:
- subject
- purpose
- scope
- recipient
- created_at
- expires_at where applicable
- revoked_at
- version
- audit trail

Operations:
- grant
- view
- modify
- revoke
- verify_consent

DISCLAIMER: For research and demonstration purposes only. Not clinically validated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field


class ConsentCategory(str, Enum):
    """Standardized healthcare consent categories."""
    CLINICAL_CARE = "clinical_care"
    HEALTH_SCREENING = "health_screening"
    WEARABLE_DATA = "wearable_data"
    AI_PROCESSING = "ai_processing"
    RESEARCH_ANALYTICS = "research_analytics"
    NOTIFICATIONS = "notifications"
    DATA_SHARING = "data_sharing"
    CAREGIVER_FAMILY_ACCESS = "caregiver_family_access"


class ConsentStatus(str, Enum):
    """Lifecycle status of a consent directive."""
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"


class ConsentAuditEvent(BaseModel):
    """Immutable audit trail event tracking every consent lifecycle or access operation."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: str  # GRANT | VIEW | MODIFY | REVOKE | ACCESS_VERIFIED | ACCESS_DENIED
    actor_id: str
    actor_role: str
    details: str
    recipient: Optional[str] = None
    ip_address: Optional[str] = None


class HealthcareConsent(BaseModel):
    """Standardized healthcare consent directive fulfilling DISHA and ABDM guidelines."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    subject: str = Field(description="Citizen/patient ID who owns the health data")
    category: ConsentCategory = Field(description="Consent category")
    purpose: str = Field(description="Clinical, operational, or analytics purpose code")
    scope: List[str] = Field(
        default_factory=lambda: ["vitals", "labs", "lifestyle", "risk_scores"],
        description="Granular scope of permitted data types"
    )
    recipient: str = Field(description="Authorized recipient entity, clinician, or service")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    version: str = Field(default="v1.0.0", description="Policy or template version")
    notice_id: Optional[str] = Field(default="SYS-CON-001-NOTICE-V1", description="Applicable consent notice ID presented")
    status: ConsentStatus = Field(default=ConsentStatus.ACTIVE)
    audit_trail: List[ConsentAuditEvent] = Field(default_factory=list)

    @property
    def timestamp(self) -> str:
        """Alias for created_at representing persisted consent timestamp."""
        return self.created_at

    @property
    def citizen_id(self) -> str:
        """Alias for subject providing backward compatibility with internal schemas."""
        return self.subject

    @property
    def grantee_id(self) -> str:
        """Alias for recipient providing backward compatibility with authorization engine."""
        return self.recipient

    def is_valid(self) -> bool:
        """Evaluates whether the consent directive is currently active and unexpired."""
        if self.status != ConsentStatus.ACTIVE:
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at:
            try:
                exp_dt = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if exp_dt < now:
                    return False
            except Exception:
                pass
        return True

    def revoke(
        self,
        reason: str,
        actor_id: str,
        actor_role: str = "CITIZEN",
    ) -> None:
        """Revokes the consent directive immediately with immutable audit event."""
        self.status = ConsentStatus.REVOKED
        self.revoked_at = datetime.now(timezone.utc).isoformat()
        self.audit_trail.append(
            ConsentAuditEvent(
                action="REVOKE",
                actor_id=actor_id,
                actor_role=actor_role,
                details=f"Consent revoked by {actor_role} ({actor_id}). Reason: {reason}",
                recipient=self.recipient,
            )
        )

    def modify(
        self,
        new_scope: Optional[List[str]] = None,
        new_expires_at: Optional[str] = None,
        new_purpose: Optional[str] = None,
        actor_id: str = "system",
        actor_role: str = "CITIZEN",
    ) -> None:
        """Modifies consent scope or expiration date with audit logging."""
        changes = []
        if new_scope is not None:
            old_s = list(self.scope)
            self.scope = new_scope
            changes.append(f"scope changed from {old_s} to {new_scope}")
        if new_expires_at is not None:
            old_exp = self.expires_at
            self.expires_at = new_expires_at
            changes.append(f"expires_at changed from {old_exp} to {new_expires_at}")
        if new_purpose is not None:
            old_p = self.purpose
            self.purpose = new_purpose
            changes.append(f"purpose changed from '{old_p}' to '{new_purpose}'")

        self.audit_trail.append(
            ConsentAuditEvent(
                action="MODIFY",
                actor_id=actor_id,
                actor_role=actor_role,
                details=f"Consent modified: {', '.join(changes)}",
                recipient=self.recipient,
            )
        )

    def record_access(
        self,
        actor_id: str,
        actor_role: str,
        details: str,
        granted: bool = True,
    ) -> None:
        """Logs data access verification against this consent directive."""
        self.audit_trail.append(
            ConsentAuditEvent(
                action="ACCESS_VERIFIED" if granted else "ACCESS_DENIED",
                actor_id=actor_id,
                actor_role=actor_role,
                details=details,
                recipient=self.recipient,
            )
        )


class ConsentManager:
    """Centralized manager for granting, viewing, modifying, revoking, and verifying consents."""

    def __init__(self):
        # In-memory storage: id -> HealthcareConsent
        self._consents_by_id: Dict[str, HealthcareConsent] = {}
        # subject -> list of consent_ids
        self._subject_index: Dict[str, List[str]] = {}

    def grant(
        self,
        subject: str,
        category: Union[ConsentCategory, str],
        purpose: str,
        scope: Optional[List[str]] = None,
        recipient: str = "PRIMARY_CARE_TEAM",
        expires_at: Optional[Union[str, datetime]] = None,
        version: str = "v1.0.0",
        notice_id: Optional[str] = "SYS-CON-001-NOTICE-V1",
        actor_id: str = "citizen",
        actor_role: str = "CITIZEN",
    ) -> HealthcareConsent:
        """Grants a new healthcare consent directive."""
        cat_enum = ConsentCategory(category) if isinstance(category, str) else category
        exp_str = expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at
        scope_list = scope or ["vitals", "labs", "lifestyle", "risk_scores"]

        consent = HealthcareConsent(
            subject=subject,
            category=cat_enum,
            purpose=purpose,
            scope=scope_list,
            recipient=recipient,
            expires_at=exp_str,
            version=version,
            notice_id=notice_id,
            status=ConsentStatus.ACTIVE,
        )

        # Initial audit log
        consent.audit_trail.append(
            ConsentAuditEvent(
                action="GRANT",
                actor_id=actor_id,
                actor_role=actor_role,
                details=f"Consent granted for category '{cat_enum.value}' with purpose '{purpose}' to recipient '{recipient}'",
                recipient=recipient,
            )
        )

        self._consents_by_id[consent.id] = consent
        if subject not in self._subject_index:
            self._subject_index[subject] = []
        self._subject_index[subject].append(consent.id)

        return consent

    def view(self, consent_id: str) -> Optional[HealthcareConsent]:
        """Views a specific consent directive by ID."""
        return self._consents_by_id.get(consent_id)

    def list_consents(
        self,
        subject: str,
        category: Optional[Union[ConsentCategory, str]] = None,
        status: Optional[Union[ConsentStatus, str]] = None,
    ) -> List[HealthcareConsent]:
        """Lists consent directives for a citizen, with optional category/status filters."""
        consent_ids = self._subject_index.get(subject, [])
        results = []

        cat_val = category.value if isinstance(category, ConsentCategory) else category
        stat_val = status.value if isinstance(status, ConsentStatus) else status

        for cid in consent_ids:
            c = self._consents_by_id.get(cid)
            if not c:
                continue
            if cat_val and c.category.value != cat_val:
                continue
            if stat_val and c.status.value != stat_val:
                continue
            results.append(c)

        return results

    def modify(
        self,
        consent_id: str,
        new_scope: Optional[List[str]] = None,
        new_expires_at: Optional[Union[str, datetime]] = None,
        new_purpose: Optional[str] = None,
        actor_id: str = "citizen",
        actor_role: str = "CITIZEN",
    ) -> HealthcareConsent:
        """Modifies scope, expiration, or purpose of an active consent directive."""
        consent = self._consents_by_id.get(consent_id)
        if not consent:
            raise KeyError(f"Consent {consent_id} not found.")

        exp_str = new_expires_at.isoformat() if isinstance(new_expires_at, datetime) else new_expires_at
        consent.modify(
            new_scope=new_scope,
            new_expires_at=exp_str,
            new_purpose=new_purpose,
            actor_id=actor_id,
            actor_role=actor_role,
        )
        return consent

    def revoke(
        self,
        consent_id: str,
        reason: str = "Revoked by citizen request",
        actor_id: str = "citizen",
        actor_role: str = "CITIZEN",
    ) -> HealthcareConsent:
        """Revokes an existing consent directive."""
        consent = self._consents_by_id.get(consent_id)
        if not consent:
            raise KeyError(f"Consent {consent_id} not found.")

        consent.revoke(reason=reason, actor_id=actor_id, actor_role=actor_role)
        return consent

    def verify_consent(
        self,
        subject: str,
        category: Union[ConsentCategory, str],
        purpose: Optional[str] = None,
        recipient: Optional[str] = None,
        required_scope: Optional[str] = None,
        actor_id: str = "ai_agent",
        actor_role: str = "AI_SYSTEM",
    ) -> Tuple[bool, str]:
        """Verifies if there is an active, valid consent directive for the subject, category, and purpose."""
        cat_enum = ConsentCategory(category) if isinstance(category, str) else category
        consents = self.list_consents(subject=subject, category=cat_enum, status=ConsentStatus.ACTIVE)

        valid_consents = [c for c in consents if c.is_valid()]

        if not valid_consents:
            return (
                False,
                f"Consent verification failed: No active consent for subject '{subject}' under category '{cat_enum.value}'."
            )

        # Purpose check to prevent purpose mismatch (TC-CON-003)
        if purpose:
            def _norm(p: Optional[str]) -> str:
                return (p or "").upper().replace("-", "_").replace(" ", "_")
            req_p = _norm(purpose)
            matched_by_purpose = []
            for c in valid_consents:
                dir_p = _norm(c.purpose)
                if dir_p in ("*", "ALL") or dir_p == req_p:
                    matched_by_purpose.append(c)
                elif req_p in ("CARE_DELIVERY", "CLINICAL_CARE") and dir_p in ("CARE_DELIVERY", "CLINICAL_CARE"):
                    matched_by_purpose.append(c)
                elif req_p in ("HEALTH_SCREENING", "SCREENING") and dir_p in ("HEALTH_SCREENING", "SCREENING"):
                    matched_by_purpose.append(c)
                elif req_p in ("RESEARCH_ANALYTICS", "RESEARCH") and dir_p in ("RESEARCH_ANALYTICS", "RESEARCH"):
                    matched_by_purpose.append(c)
            if not matched_by_purpose:
                return (
                    False,
                    f"Consent purpose mismatch: Active directive was granted for purpose '{valid_consents[0].purpose}', but requested purpose is '{purpose}'. Processing outside granted scope is denied."
                )
            valid_consents = matched_by_purpose

        # Check recipient if specified
        matched_consent = None
        for c in valid_consents:
            if recipient is None or c.recipient == recipient or c.recipient in ["*", "ALL", "PRIMARY_CARE_TEAM", "AI_AGENT"]:
                # Check scope if specified
                if required_scope is None or required_scope in c.scope:
                    matched_consent = c
                    break

        if not matched_consent:
            if required_scope:
                return (
                    False,
                    f"Consent scope mismatch: Requested data scope '{required_scope}' is outside permitted scopes {valid_consents[0].scope}."
                )
            matched_consent = valid_consents[0]

        matched_consent.record_access(
            actor_id=actor_id,
            actor_role=actor_role,
            details=f"Verified active consent for {cat_enum.value} access (purpose: {purpose or matched_consent.purpose}, recipient: {recipient or 'ANY'})",
            granted=True,
        )

        return True, f"Consent verified: Active directive {matched_consent.id} found."


# Global singleton instance
consent_manager = ConsentManager()
