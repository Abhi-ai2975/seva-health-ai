"""Referral Clinical Model with Configured State Machine & Lifecycle History.

Implements TC-CLN-003:
- Permitted state machine workflow: PENDING -> ASSIGNED -> IN_PROGRESS -> COMPLETED (or CANCELLED)
- Preserves full transition audit history (from_status, to_status, actor, timestamp, notes)
- Enforces responsible destination / party assignment
- Captures final consultation outcome, diagnosis, and treatment plan
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid

from packages.types.enums import ReferralStatus, TriageUrgency


PERMITTED_REFERRAL_TRANSITIONS: Dict[ReferralStatus, List[ReferralStatus]] = {
    ReferralStatus.PENDING: [ReferralStatus.ASSIGNED, ReferralStatus.CANCELLED],
    ReferralStatus.ASSIGNED: [ReferralStatus.IN_PROGRESS, ReferralStatus.CANCELLED],
    ReferralStatus.IN_PROGRESS: [ReferralStatus.COMPLETED, ReferralStatus.CANCELLED],
    ReferralStatus.COMPLETED: [],  # Terminal state
    ReferralStatus.CANCELLED: [],  # Terminal state
}


class ReferralTransitionRecord(BaseModel):
    """Immutable transition audit record for referral lifecycle history (TC-CLN-003)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    from_status: ReferralStatus
    to_status: ReferralStatus
    transitioned_by: str
    transitioned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Referral(BaseModel):
    """Complete referral entity with strict state machine and audit trail."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    referral_slip_id: str
    citizen_id: str
    tenant_id: str = "karnataka_state_health"
    referring_provider_id: str
    referring_provider_role: str = "CLINICIAN"  # CLINICIAN | HEALTH_WORKER
    urgency: str = "ROUTINE"                    # ROUTINE | PRIORITY | URGENT | EMERGENT
    reason: str
    provisional_diagnosis: Optional[str] = None
    clinical_notes: Optional[str] = None
    
    # Destination & Responsible party (Assigned in ASSIGNED state)
    assigned_facility_id: Optional[str] = None
    assigned_facility_name: Optional[str] = None
    assigned_department: Optional[str] = None
    assigned_clinician_id: Optional[str] = None
    assigned_clinician_name: Optional[str] = None
    
    # Lifecycle state machine & transition history
    status: ReferralStatus = ReferralStatus.PENDING
    history: List[ReferralTransitionRecord] = Field(default_factory=list)
    
    # Final consultation outcome (Recorded in COMPLETED state)
    outcome_summary: Optional[str] = None
    outcome_diagnosis: Optional[str] = None
    treatment_plan: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(
        self,
        new_status: ReferralStatus,
        actor_id: str,
        notes: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReferralTransitionRecord:
        """Transitions referral to new status strictly following the configured state machine."""
        allowed = PERMITTED_REFERRAL_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            allowed_names = [s.value for s in allowed]
            raise ValueError(
                f"Invalid referral state transition from '{self.status.value}' to '{new_status.value}'. "
                f"Permitted next transitions: {allowed_names or 'None (Terminal state)'}."
            )
        
        transition = ReferralTransitionRecord(
            from_status=self.status,
            to_status=new_status,
            transitioned_by=actor_id,
            transitioned_at=datetime.now(timezone.utc),
            notes=notes,
            metadata=metadata or {},
        )
        self.history.append(transition)
        self.status = new_status
        self.updated_at = transition.transitioned_at
        return transition


class ReferralCreatePayload(BaseModel):
    citizen_id: str
    reason: str
    urgency: str = "ROUTINE"  # ROUTINE | PRIORITY | URGENT | EMERGENT
    provisional_diagnosis: Optional[str] = None
    clinical_notes: Optional[str] = None
    facility_name: Optional[str] = None


class ReferralAssignPayload(BaseModel):
    assigned_facility_name: str
    assigned_facility_id: Optional[str] = "FAC-MYS-DH-01"
    assigned_department: Optional[str] = "Internal Medicine & NCD Special Clinic"
    assigned_clinician_id: Optional[str] = None
    assigned_clinician_name: Optional[str] = None
    notes: Optional[str] = "Destination facility and responsible specialist assigned"


class ReferralTransitionPayload(BaseModel):
    target_status: ReferralStatus
    notes: Optional[str] = None


class ReferralClosePayload(BaseModel):
    outcome_summary: str
    outcome_diagnosis: Optional[str] = None
    treatment_plan: Optional[str] = None
    notes: Optional[str] = "Referral completed and closed with clinical outcome"
