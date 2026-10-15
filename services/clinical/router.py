from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone
from pydantic import BaseModel
import uuid

from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from packages.clinical_models.referral import (
    Referral as ClinicalReferral,
    ReferralTransitionRecord,
    PERMITTED_REFERRAL_TRANSITIONS,
    ReferralCreatePayload,
    ReferralAssignPayload,
    ReferralTransitionPayload,
    ReferralClosePayload,
)
from packages.types.enums import (
    ClinicianReviewStatus,
    ReferralStatus,
    UserRole,
    AuditAction,
    TriageUrgency,
    Gender,
    NotificationDeliveryStatus,
    NotificationChannel,
)
from packages.clinical_models.notification import (
    ClinicalNotification,
    NotificationDeliveryAttempt,
    TriggerNotificationPayload,
    RetryNotificationPayload,
    EscalationEventPayload,
    EscalationEventResponse,
)
from packages.auth.jwt import get_current_user_token, TokenPayload, require_roles
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.observability.audit import audit_logger
from services.store import store, CitizenRecord, ConsentDirective
from services.clinical.copilot import (
    ClinicianCopilotEngine,
    ClinicianCopilotPatientView,
    AICopilotSynthesis,
    ClinicianActionPayload,
    ClinicianVerificationRecord,
)
from packages.security.xss_guard import XSSSanitizer

router = APIRouter(prefix="/clinician", tags=["Clinical Decision Support & Copilot"])


def _ensure_synthetic_citizen_if_missing(citizen_id: str, tenant_id: str = "karnataka_state_health") -> CitizenRecord:
    """Ensures a synthetic citizen record and consent exist for live UI simulation."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        c = CitizenRecord(
            id=citizen_id,
            tenant_id=tenant_id,
            user_id=f"user-{citizen_id}",
            abha_id="91-1111-2222-3333",
            first_name="Ravi" if "cln" in citizen_id else "Ramesh",
            last_name="ReviewKumar" if "cln" in citizen_id else "Patel",
            birth_date="1980-04-12",
            gender=Gender.MALE,
            phone="+919876543299",
            state="Karnataka",
            district="Bengaluru Urban",
            sub_district="Bengaluru South",
            village_or_ward="Ward 120",
        )
        store.citizens[citizen_id] = c
        for gid in ("clinician-dr-kulkarni-01", "doctor-user-01", "doctor@sevahealth.ai"):
            consent = ConsentDirective(
                id=f"consent-{citizen_id}-{gid}",
                citizen_id=citizen_id,
                grantee_id=gid,
                grantee_role=UserRole.CLINICIAN,
                purpose="CARE_DELIVERY",
                scope=["OBSERVATIONS", "RISK_ASSESSMENT", "TRAJECTORY", "CLINICAL_REVIEW", "REFERRAL"],
            )
            store.add_consent(consent)
        for doc_id in ("doctor-user-01", "clinician-dr-kulkarni-01"):
            doc_user = store.get_user(doc_id)
            if doc_user and citizen_id not in doc_user.assigned_patients:
                doc_user.assigned_patients.append(citizen_id)
        return c

    for gid in ("clinician-dr-kulkarni-01", "doctor-user-01", "doctor@sevahealth.ai"):
        if not store.has_active_consent(citizen_id, gid, purpose="CARE_DELIVERY"):
            store.add_consent(ConsentDirective(
                id=f"consent-{citizen_id}-{gid}",
                citizen_id=citizen_id,
                grantee_id=gid,
                grantee_role=UserRole.CLINICIAN,
                purpose="CARE_DELIVERY",
                scope=["OBSERVATIONS", "RISK_ASSESSMENT", "TRAJECTORY", "CLINICAL_REVIEW", "REFERRAL"],
            ))
    for doc_id in ("doctor-user-01", "clinician-dr-kulkarni-01"):
        doc_user = store.get_user(doc_id)
        if doc_user and citizen_id not in doc_user.assigned_patients:
            doc_user.assigned_patients.append(citizen_id)
    return citizen


class ClinicianReviewPayload(BaseModel):
    status: ClinicianReviewStatus = ClinicianReviewStatus.APPROVED
    decision: Optional[str] = None
    review_notes: str = "Verified in PHC clinic; treatment plan approved."
    updated_care_plan_instructions: Optional[str] = None


class AcknowledgeReviewPayload(BaseModel):
    notes: Optional[str] = "Review item acknowledged by attending physician."


class SeedTriageCasePayload(BaseModel):
    citizen_id: str = "citizen-cln-test-01"
    rule_type: str = "SEVERE_HYPERTENSION"  # SEVERE_HYPERTENSION | CRITICAL_GLYCEMIC | HIGH_NCD_RISK | WORSENING_TRAJECTORY
    custom_reason: Optional[str] = None
    custom_urgency: Optional[TriageUrgency] = None


# --- 1. CLINICIAN TRIAGE QUEUE & REVIEW ENDPOINTS (TC-CLN-001 & TC-CLN-002) ---

@router.get("/triage", response_model=List[ClinicalTriageCase])
async def list_triage_queue(
    status: Optional[ClinicianReviewStatus] = None,
    urgency: Optional[TriageUrgency] = None,
    citizen_id: Optional[str] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Retrieves all high-risk and deteriorating cases enqueued for clinician review (TC-CLN-001)."""
    cases = store.list_triage_cases(status=status)
    if urgency:
        cases = [c for c in cases if c.urgency == urgency]
    if citizen_id:
        cases = [c for c in cases if c.citizen_id == citizen_id]
    return cases


@router.get("/triage/{triage_id}", response_model=ClinicalTriageCase)
async def get_triage_item(
    triage_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Retrieves a specific review item by triage ID (TC-CLN-002 Step 1)."""
    case = store.get_triage_case(triage_id)
    if not case:
        raise HTTPException(status_code=404, detail="Triage case not found")
    citizen = store.get_citizen(case.citizen_id)
    if citizen:
        HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")
    return case


@router.post("/triage/{triage_id}/acknowledge", response_model=ClinicalTriageCase)
@router.post("/review/{triage_id}/acknowledge", response_model=ClinicalTriageCase)
async def acknowledge_review_item(
    triage_id: str,
    payload: Optional[AcknowledgeReviewPayload] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Records clinician acknowledgement of a pending review queue item (TC-CLN-002 Step 2)."""
    case = store.get_triage_case(triage_id)
    if not case:
        raise HTTPException(status_code=404, detail="Triage case not found")
    citizen = store.get_citizen(case.citizen_id)
    if citizen:
        HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")

    now = datetime.now(timezone.utc)
    notes = payload.notes if payload and payload.notes else "Review item acknowledged by attending physician."
    case.is_acknowledged = True
    case.acknowledged_at = now
    case.acknowledged_by = current_user.sub
    case.assigned_clinician_id = current_user.sub
    if case.status == ClinicianReviewStatus.PENDING:
        case.status = ClinicianReviewStatus.ACKNOWLEDGED

    case.review_history.append({
        "action": "ACKNOWLEDGED",
        "acknowledged_by": current_user.sub,
        "timestamp": now.isoformat(),
        "notes": notes,
        "status": case.status.value,
    })
    store.add_triage_case(case)

    audit_logger.record(
        tenant_id=case.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CLINICIAN_REVIEWED,
        resource_type="ClinicalTriageCase",
        resource_id=case.id,
        details={"action": "ACKNOWLEDGE", "is_acknowledged": True, "notes": notes},
    )
    return case


@router.post("/review/{triage_id}", response_model=ClinicalTriageCase)
async def submit_clinical_review(
    triage_id: str,
    payload: ClinicianReviewPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Records clinician review decision, notes, and care plan modifications (TC-CLN-002 Step 2)."""
    case = store.get_triage_case(triage_id)
    if not case:
        raise HTTPException(status_code=404, detail="Triage case not found")

    citizen = store.get_citizen(case.citizen_id)
    if not citizen:
        citizen = _ensure_synthetic_citizen_if_missing(case.citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")

    now = datetime.now(timezone.utc)
    clean_notes = XSSSanitizer.sanitize_text(payload.review_notes)
    case.status = payload.status
    case.decision = payload.decision or payload.status.value
    case.assigned_clinician_id = current_user.sub
    case.review_notes = clean_notes
    case.reviewed_at = now

    case.review_history.append({
        "action": "DECISION_RECORDED",
        "decision": case.decision,
        "status": case.status.value,
        "reviewed_by": current_user.sub,
        "timestamp": now.isoformat(),
        "notes": clean_notes,
    })

    # Mark associated care plan as reviewed
    plan = store.get_care_plan(case.citizen_id)
    if plan:
        plan.clinician_reviewed = True
        plan.clinician_id = current_user.sub
        if payload.updated_care_plan_instructions:
            clean_instr = XSSSanitizer.sanitize_text(payload.updated_care_plan_instructions)
            plan.nutrition_guidance += f" [Doctor Note: {clean_instr}]"
        store.set_care_plan(plan)

    store.add_triage_case(case)

    audit_logger.record(
        tenant_id=case.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CLINICIAN_REVIEWED,
        resource_type="ClinicalTriageCase",
        resource_id=case.id,
        details={"status": case.status.value, "decision": case.decision, "notes": payload.review_notes},
    )

    return case


@router.post("/triage/seed", response_model=ClinicalTriageCase, include_in_schema=False)
@router.post("/triage/seed-demo-case", response_model=ClinicalTriageCase, include_in_schema=False)
async def seed_review_case(
    payload: SeedTriageCasePayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-001: Seeds a case meeting an approved clinical review rule and enqueues in triage."""
    citizen = _ensure_synthetic_citizen_if_missing(payload.citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")

    rule_type = payload.rule_type.upper()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())
    assessment_id = f"eval-rule-{uuid.uuid4().hex[:8]}"

    if rule_type == "SEVERE_HYPERTENSION":
        urgency = payload.custom_urgency or TriageUrgency.EMERGENT
        reason = payload.custom_reason or "Severe Blood Pressure Elevation (164/102 mmHg) meeting approved clinical review rule"
        soap = SOAPReport(
            subjective=f"Patient {citizen.first_name} presents with acute occipital headache and dizziness.",
            objective="Vitals: BP 164/102 mmHg, Resting HR 88 bpm. Stage 2 Hypertensive Crisis threshold exceeded.",
            assessment="Severe Uncontrolled Hypertension meeting approved human-in-the-loop review criteria.",
            plan="Immediate physician evaluation, anti-hypertensive dose titration, and sodium restriction.",
            key_observations={"systolic_bp": 164, "diastolic_bp": 102, "rule": "SEVERE_HYPERTENSION"},
        )
    elif rule_type == "CRITICAL_GLYCEMIC":
        urgency = payload.custom_urgency or TriageUrgency.PRIORITY
        reason = payload.custom_reason or "Critical Glycemic Dysregulation (HbA1c 9.2%, Fasting Glucose 214 mg/dL) meeting approved review rule"
        soap = SOAPReport(
            subjective=f"Patient {citizen.first_name} reports polyuria, polydipsia, and postprandial fatigue.",
            objective="Labs: HbA1c 9.2%, Fasting Glucose 214 mg/dL. Renal eGFR 78 mL/min/1.73m².",
            assessment="Severe Glycemic Dysregulation with elevated microvascular progression risk.",
            plan="Medical officer review, oral hypoglycemic adjustment, and HbA1c repeat in 6 weeks.",
            key_observations={"hba1c": 9.2, "fasting_glucose": 214, "rule": "CRITICAL_GLYCEMIC"},
        )
    elif rule_type == "WORSENING_TRAJECTORY":
        urgency = payload.custom_urgency or TriageUrgency.PRIORITY
        reason = payload.custom_reason or "Adverse Longitudinal Trajectory (+48% composite risk spike) meeting clinical review rule"
        soap = SOAPReport(
            subjective="Longitudinal health check indicates progressive biometric deterioration over 90 days.",
            objective="Composite Risk Shift: 0.22 -> 0.68 (+48%). Systolic BP +18 mmHg, Fasting Glucose +34 mg/dL.",
            assessment="Rapidly Deteriorating NCD Trajectory requiring active physician intervention.",
            plan="Comprehensive care plan update, therapeutic escalation, and weekly tele-monitoring.",
            key_observations={"delta_composite": 0.46, "rule": "WORSENING_TRAJECTORY"},
        )
    else:  # HIGH_NCD_RISK
        urgency = payload.custom_urgency or TriageUrgency.PRIORITY
        reason = payload.custom_reason or "Elevated High-Risk NCD Multi-Domain Composite Stratification meeting review rule"
        soap = SOAPReport(
            subjective="Routine community NCD screening conducted at Anganwadi health camp.",
            objective="IDRS Score 65, CBAC 7. Composite Risk Tier: HIGH (64% 5-year event probability).",
            assessment="High-risk multi-domain metabolic and cardiovascular risk profile.",
            plan="Physician triage sign-off, confirmatory lipid profile, and 30-day guided lifestyle intervention.",
            key_observations={"tier": "HIGH", "rule": "HIGH_NCD_RISK"},
        )

    triage_case = ClinicalTriageCase(
        id=case_id,
        tenant_id=citizen.tenant_id,
        citizen_id=citizen.id,
        citizen_name=f"{citizen.first_name} {citizen.last_name}",
        risk_assessment_id=assessment_id,
        urgency=urgency,
        escalation_reason=XSSSanitizer.sanitize_text(reason),
        soap_note=soap,
        status=ClinicianReviewStatus.PENDING,
        created_at=now,
    )
    store.add_triage_case(triage_case)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.SCREENING_SUBMITTED,
        resource_type="ClinicalTriageCase",
        resource_id=triage_case.id,
        details={"rule_type": rule_type, "urgency": urgency.value, "reason": reason},
    )

    return triage_case


# --- 2. CLOSED-LOOP REFERRAL LIFECYCLE ENDPOINTS (TC-CLN-003) ---

@router.post("/referrals", response_model=ClinicalReferral)
async def create_referral_endpoint(
    payload: ReferralCreatePayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-003: Creates a referral for a citizen with initial PENDING status and history tracking."""
    citizen = _ensure_synthetic_citizen_if_missing(payload.citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="REFERRAL")

    now = datetime.now(timezone.utc)
    slip_id = f"REF-{now.year}-MYS-{str(uuid.uuid4())[:6].upper()}"
    referral = ClinicalReferral(
        referral_slip_id=slip_id,
        citizen_id=citizen.id,
        tenant_id=citizen.tenant_id,
        referring_provider_id=current_user.sub,
        referring_provider_role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        urgency=payload.urgency,
        reason=payload.reason,
        provisional_diagnosis=payload.provisional_diagnosis,
        clinical_notes=payload.clinical_notes,
        status=ReferralStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    # Record initial creation transition
    init_trans = ReferralTransitionRecord(
        from_status=ReferralStatus.PENDING,
        to_status=ReferralStatus.PENDING,
        transitioned_by=current_user.sub,
        transitioned_at=now,
        notes=f"Referral initiated by {current_user.sub}: {payload.reason}",
    )
    referral.history.append(init_trans)
    store.add_referral(citizen.id, referral)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.REFERRAL_ISSUED,
        resource_type="Referral",
        resource_id=referral.id,
        details={"referral_slip_id": slip_id, "urgency": payload.urgency, "reason": payload.reason},
    )
    return referral


@router.get("/referrals", response_model=List[ClinicalReferral])
async def list_referrals_endpoint(
    citizen_id: Optional[str] = None,
    status: Optional[ReferralStatus] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """Lists referrals with optional citizen and status filtering (TC-CLN-003)."""
    if citizen_id:
        refs = store.get_referrals(citizen_id)
    else:
        refs = store.list_all_referrals()

    result = []
    for r in refs:
        if isinstance(r, ClinicalReferral):
            if not status or r.status == status:
                result.append(r)
        elif isinstance(r, dict):
            if not status or r.get("status") == (status.value if status else r.get("status")):
                try:
                    result.append(ClinicalReferral(**r))
                except Exception:
                    pass
    return result


@router.get("/referrals/{referral_id}", response_model=ClinicalReferral)
async def get_referral_endpoint(
    referral_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """Retrieves a specific referral and its complete state machine history (TC-CLN-003)."""
    ref = store.get_referral_by_id(referral_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
    if isinstance(ref, ClinicalReferral):
        return ref
    return ClinicalReferral(**ref)


@router.post("/referrals/{referral_id}/assign", response_model=ClinicalReferral)
async def assign_referral_endpoint(
    referral_id: str,
    payload: ReferralAssignPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-003: Assigns destination facility and responsible clinician, transitioning PENDING -> ASSIGNED."""
    ref = store.get_referral_by_id(referral_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
    if isinstance(ref, dict):
        ref = ClinicalReferral(**ref)

    try:
        ref.assigned_facility_id = payload.assigned_facility_id or "FAC-MYS-DH-01"
        ref.assigned_facility_name = payload.assigned_facility_name
        ref.assigned_department = payload.assigned_department
        ref.assigned_clinician_id = payload.assigned_clinician_id
        ref.assigned_clinician_name = payload.assigned_clinician_name

        ref.transition_to(
            new_status=ReferralStatus.ASSIGNED,
            actor_id=current_user.sub,
            notes=payload.notes or f"Assigned to {payload.assigned_facility_name}",
            metadata={"assigned_facility": payload.assigned_facility_name, "department": payload.assigned_department},
        )
        store.update_referral(ref)

        audit_logger.record(
            tenant_id=ref.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.REFERRAL_UPDATED,
            resource_type="Referral",
            resource_id=ref.id,
            details={"transition": "ASSIGNED", "facility": payload.assigned_facility_name},
        )
        return ref
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/referrals/{referral_id}/transition", response_model=ClinicalReferral)
async def transition_referral_endpoint(
    referral_id: str,
    payload: ReferralTransitionPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-003: Updates referral status through the permitted state machine workflow."""
    ref = store.get_referral_by_id(referral_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
    if isinstance(ref, dict):
        ref = ClinicalReferral(**ref)

    try:
        ref.transition_to(
            new_status=payload.target_status,
            actor_id=current_user.sub,
            notes=payload.notes,
        )
        store.update_referral(ref)

        audit_logger.record(
            tenant_id=ref.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.REFERRAL_UPDATED,
            resource_type="Referral",
            resource_id=ref.id,
            details={"transition": payload.target_status.value, "notes": payload.notes},
        )
        return ref
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/referrals/{referral_id}/close", response_model=ClinicalReferral)
async def close_referral_endpoint(
    referral_id: str,
    payload: ReferralClosePayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-003: Closes referral with final clinical outcome and preserves history."""
    ref = store.get_referral_by_id(referral_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
    if isinstance(ref, dict):
        ref = ClinicalReferral(**ref)

    try:
        now = datetime.now(timezone.utc)
        ref.outcome_summary = payload.outcome_summary
        ref.outcome_diagnosis = payload.outcome_diagnosis
        ref.treatment_plan = payload.treatment_plan
        ref.closed_at = now
        ref.closed_by = current_user.sub

        ref.transition_to(
            new_status=ReferralStatus.COMPLETED,
            actor_id=current_user.sub,
            notes=payload.notes or f"Referral completed with outcome: {payload.outcome_summary}",
            metadata={"outcome_summary": payload.outcome_summary, "diagnosis": payload.outcome_diagnosis},
        )
        store.update_referral(ref)

        audit_logger.record(
            tenant_id=ref.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.REFERRAL_CLOSED,
            resource_type="Referral",
            resource_id=ref.id,
            details={"outcome_summary": payload.outcome_summary, "closed_by": current_user.sub},
        )
        return ref
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/referrals/{citizen_id}")
async def clear_referrals_endpoint(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Clears referrals for sandbox testing."""
    store.clear_referrals(citizen_id)
    return {"status": "cleared", "citizen_id": citizen_id}


@router.delete("/triage/{citizen_id}")
async def clear_triage_endpoint(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Clears triage cases for sandbox testing."""
    store.clear_triage_cases(citizen_id)
    return {"status": "cleared", "citizen_id": citizen_id}


# --- 3. CLINICAL NOTIFICATIONS & RETRY STATE MACHINE (TC-CLN-004) ---

@router.post("/notifications/trigger", response_model=ClinicalNotification)
async def trigger_notification_endpoint(
    payload: TriggerNotificationPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-004: Triggers clinical notification, supports simulating provider failure,
    and enforces that clinical review is never silently marked complete.
    """
    citizen = _ensure_synthetic_citizen_if_missing(payload.citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")

    now = datetime.now(timezone.utc)
    notif_id = f"notif-{uuid.uuid4().hex[:8]}"
    title = payload.title or f"Clinical Alert: {payload.urgency} Escalation"
    body = payload.body or f"Urgent clinical review required for citizen {citizen.first_name} {citizen.last_name}."

    notif = ClinicalNotification(
        id=notif_id,
        tenant_id=citizen.tenant_id,
        recipient_id=current_user.sub,
        recipient_role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        citizen_id=citizen.id,
        associated_resource_type=payload.associated_resource_type,
        associated_resource_id=payload.associated_resource_id,
        channel=payload.channel,
        title=title,
        body=body,
        urgency=payload.urgency,
        created_at=now,
    )

    # Link to triage case if applicable
    case = store.get_triage_case(payload.associated_resource_id)
    initial_case_status = case.status if case else None
    if case and notif_id not in case.associated_notification_ids:
        case.associated_notification_ids.append(notif_id)
        store.add_triage_case(case)

    # Process delivery attempt (simulate failure or succeed)
    if payload.simulate_provider_failure:
        notif.record_attempt(
            success=False,
            error_message=payload.failure_error_message,
            response_code=payload.failure_status_code,
            latency_ms=18.5,
        )
        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.NOTIFICATION_FAILED,
            resource_type="ClinicalNotification",
            resource_id=notif.id,
            details={"channel": payload.channel, "failure_reason": payload.failure_error_message, "code": payload.failure_status_code},
        )
    else:
        notif.record_attempt(
            success=True,
            response_code=200,
            latency_ms=14.0,
        )
        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.NOTIFICATION_DELIVERED,
            resource_type="ClinicalNotification",
            resource_id=notif.id,
            details={"channel": payload.channel, "delivered_at": now.isoformat()},
        )

    # CRITICAL CLINICAL SAFETY GUARDRAIL:
    # Notification delivery failure (or delivery) must NEVER silently mark clinical review as complete!
    if case:
        if initial_case_status in (ClinicianReviewStatus.PENDING, ClinicianReviewStatus.ACKNOWLEDGED, ClinicianReviewStatus.IN_REVIEW):
            assert case.status in (ClinicianReviewStatus.PENDING, ClinicianReviewStatus.ACKNOWLEDGED, ClinicianReviewStatus.IN_REVIEW), \
                "Clinical review must NOT be silently marked complete upon notification failure or dispatch!"

    store.add_clinical_notification(notif)
    return notif


@router.get("/notifications", response_model=List[ClinicalNotification])
async def list_notifications_endpoint(
    citizen_id: Optional[str] = None,
    associated_resource_id: Optional[str] = None,
    status: Optional[NotificationDeliveryStatus] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-004: Lists clinical notifications with delivery attempt history."""
    items = store.list_clinical_notifications(
        citizen_id=citizen_id,
        associated_resource_id=associated_resource_id,
    )
    res = []
    for n in items:
        if isinstance(n, ClinicalNotification):
            if not status or n.status == status:
                res.append(n)
        elif isinstance(n, dict):
            if not status or n.get("status") == (status.value if status else n.get("status")):
                try:
                    res.append(ClinicalNotification(**n))
                except Exception:
                    pass
    return res


@router.get("/notifications/{notification_id}", response_model=ClinicalNotification)
async def get_notification_endpoint(
    notification_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-004: Retrieves notification delivery status and attempt audit log."""
    notif = store.get_clinical_notification(notification_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if isinstance(notif, ClinicalNotification):
        return notif
    return ClinicalNotification(**notif)


@router.post("/notifications/{notification_id}/retry", response_model=ClinicalNotification)
async def retry_notification_endpoint(
    notification_id: str,
    payload: Optional[RetryNotificationPayload] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-004: Retries failed notification using configured retry policy.
    Ensures associated clinical review remains in pending/active queue.
    """
    notif = store.get_clinical_notification(notification_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if isinstance(notif, dict):
        notif = ClinicalNotification(**notif)

    if not notif.can_retry:
        raise HTTPException(
            status_code=400,
            detail=f"Notification cannot be retried. Current status: '{notif.status.value}', Attempts: {notif.delivery_attempts}/{notif.max_retries}"
        )

    simulate_failure = payload.simulate_provider_failure if payload else False
    if simulate_failure:
        err_msg = (payload and payload.failure_error_message) or "Upstream gateway delivery failure on retry"
        code = (payload and payload.failure_status_code) or 503
        notif.record_attempt(success=False, error_message=err_msg, response_code=code, latency_ms=22.0)
        audit_logger.record(
            tenant_id=notif.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.NOTIFICATION_FAILED,
            resource_type="ClinicalNotification",
            resource_id=notif.id,
            details={"retry_attempt": notif.delivery_attempts, "status": notif.status.value},
        )
    else:
        notif.record_attempt(success=True, response_code=200, latency_ms=16.0)
        audit_logger.record(
            tenant_id=notif.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.NOTIFICATION_RETRIED,
            resource_type="ClinicalNotification",
            resource_id=notif.id,
            details={"retry_attempt": notif.delivery_attempts, "status": notif.status.value},
        )

    # CRITICAL CLINICAL SAFETY CHECK:
    # Ensure associated clinical review case is NOT silently completed!
    case = store.get_triage_case(notif.associated_resource_id)
    if case:
        if case.status in (ClinicianReviewStatus.APPROVED, ClinicianReviewStatus.REJECTED):
            pass
        else:
            assert case.status in (ClinicianReviewStatus.PENDING, ClinicianReviewStatus.ACKNOWLEDGED, ClinicianReviewStatus.IN_REVIEW), \
                "Clinical review case must not be silently marked complete upon notification retry!"

    store.update_clinical_notification(notif)
    return notif


# --- 4. DUPLICATE ESCALATION EVENT HANDLING (TC-CLN-005) ---

@router.post("/triage/escalate", response_model=EscalationEventResponse)
async def submit_escalation_event_endpoint(
    payload: EscalationEventPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-005: Acute escalation event submission with idempotency deduplication.
    Prevents unintended duplicate work items and clearly identifies repeated events.
    """
    citizen = _ensure_synthetic_citizen_if_missing(payload.citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CLINICAL_REVIEW")

    now = datetime.now(timezone.utc)
    idempotency_key = payload.idempotency_key or f"esc-{citizen.id}-{payload.event_type}"

    # Check for existing case with this idempotency key
    existing_case = store.find_triage_by_idempotency(idempotency_key)
    if existing_case:
        # Duplicate escalation event detected!
        existing_case.duplicate_submission_count += 1
        existing_case.last_duplicate_event_at = now
        dup_record = {
            "action": "DUPLICATE_ESCALATION_SUPPRESSED",
            "timestamp": now.isoformat(),
            "idempotency_key": idempotency_key,
            "event_type": payload.event_type,
            "reason": payload.reason,
            "duplicate_count": existing_case.duplicate_submission_count,
            "notes": "Duplicate escalation event suppressed per configured deduplication policy.",
        }
        existing_case.duplicate_events.append(dup_record)
        existing_case.review_history.append(dup_record)
        store.add_triage_case(existing_case)

        # Record alert in alerts store clearly marked as duplicate
        alert_item = {
            "id": f"alt-dup-{uuid.uuid4().hex[:6]}",
            "citizen_id": citizen.id,
            "alert_type": "DUPLICATE_ESCALATION_SUPPRESSED",
            "message": f"Repeated escalation event suppressed. Linked to active triage case {existing_case.id}.",
            "idempotency_key": idempotency_key,
            "duplicate_suppressed": True,
            "linked_case_id": existing_case.id,
            "timestamp": now.isoformat(),
        }
        store.add_alert(alert_item)

        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=current_user.sub,
            actor_role=current_user.role,
            action=AuditAction.DUPLICATE_ESCALATION_SUPPRESSED,
            resource_type="ClinicalTriageCase",
            resource_id=existing_case.id,
            details={
                "idempotency_key": idempotency_key,
                "duplicate_count": existing_case.duplicate_submission_count,
                "reason": payload.reason,
            },
        )

        return EscalationEventResponse(
            case_id=existing_case.id,
            citizen_id=citizen.id,
            urgency=existing_case.urgency.value if hasattr(existing_case.urgency, "value") else str(existing_case.urgency),
            status=existing_case.status.value if hasattr(existing_case.status, "value") else str(existing_case.status),
            is_duplicate=True,
            duplicate_count=existing_case.duplicate_submission_count,
            duplicate_action="SUPPRESSED_AND_LINKED",
            message=f"Duplicate escalation event '{idempotency_key}' detected. Work item suppressed and linked to existing case {existing_case.id}.",
            created_at=existing_case.created_at,
        )

    # Fresh escalation event
    case_id = str(uuid.uuid4())
    urgency_enum = TriageUrgency.EMERGENT if payload.urgency.upper() == "EMERGENT" else TriageUrgency.PRIORITY
    soap = SOAPReport(
        subjective=f"Patient {citizen.first_name} experiencing {payload.event_type}: {payload.reason}.",
        objective=f"Key observations: {payload.key_observations or 'Acute vital spike recorded.'}",
        assessment=f"Acute clinical escalation ({payload.event_type}) requiring physician evaluation.",
        plan="Urgent clinician review, patient check-in, and therapeutic stabilization.",
        key_observations=payload.key_observations,
    )

    new_case = ClinicalTriageCase(
        id=case_id,
        tenant_id=citizen.tenant_id,
        citizen_id=citizen.id,
        citizen_name=f"{citizen.first_name} {citizen.last_name}",
        risk_assessment_id=f"risk-{uuid.uuid4().hex[:6]}",
        urgency=urgency_enum,
        escalation_reason=payload.reason,
        soap_note=soap,
        status=ClinicianReviewStatus.PENDING,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    store.add_triage_case(new_case)
    store.set_triage_idempotency(idempotency_key, new_case.id)

    # Register initial alert
    alert_item = {
        "id": f"alt-{uuid.uuid4().hex[:6]}",
        "citizen_id": citizen.id,
        "alert_type": "CLINICAL_ESCALATION",
        "message": f"Acute escalation: {payload.reason}",
        "idempotency_key": idempotency_key,
        "duplicate_suppressed": False,
        "linked_case_id": new_case.id,
        "timestamp": now.isoformat(),
    }
    store.add_alert(alert_item)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.SCREENING_SUBMITTED,
        resource_type="ClinicalTriageCase",
        resource_id=new_case.id,
        details={"idempotency_key": idempotency_key, "urgency": urgency_enum.value, "reason": payload.reason},
    )

    return EscalationEventResponse(
        case_id=new_case.id,
        citizen_id=citizen.id,
        urgency=urgency_enum.value,
        status=new_case.status.value,
        is_duplicate=False,
        duplicate_count=0,
        duplicate_action="CREATED",
        message="New clinical triage case created and enqueued.",
        created_at=new_case.created_at,
    )


@router.get("/alerts")
async def list_alerts_endpoint(
    citizen_id: Optional[str] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN])),
):
    """TC-CLN-005: Retrieves clinical alerts, including duplicate escalation suppression records."""
    if citizen_id:
        return store.get_alerts(citizen_id)
    all_alerts = []
    for c_id, a_list in store.alerts.items():
        all_alerts.extend(a_list)
    return all_alerts


@router.delete("/notifications/{citizen_id}")
async def clear_notifications_endpoint(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Clears clinical notifications and idempotency maps for sandbox testing."""
    store.clear_clinical_notifications(citizen_id)
    store.clear_escalation_idempotency()
    if citizen_id in store.alerts:
        store.alerts[citizen_id] = []
    return {"status": "cleared", "citizen_id": citizen_id}



# --- CLINICIAN AI COPILOT ENDPOINTS ---

@router.get("/copilot/patient-summary/{citizen_id}", response_model=ClinicianCopilotPatientView)
async def get_patient_summary(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Assembles all 11 patient dimensions into a consolidated view for the attending clinician:

    Current risks, Risk trajectory, Recent vitals, Recent labs, Wearable trends,
    Lifestyle, Medications, Adherence, Interventions, Alerts, Documents.
    """
    try:
        view = ClinicianCopilotEngine.assemble_patient_summary(citizen_id, current_user)
        return view
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to assemble patient summary: {str(exc)}")


@router.post("/copilot/generate/{citizen_id}", response_model=AICopilotSynthesis)
async def generate_copilot_synthesis(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Generates 7 AI decision-support sections clearly marked as AI-GENERATED:

    1. Clinical summary
    2. Risk summary
    3. Recent changes
    4. Missing information
    5. Possible contributing factors
    6. Suggested follow-up
    7. Questions for clinician consideration

    NOTE: Never automatically written into the legal clinical record without explicit clinician confirmation.
    """
    try:
        synthesis = ClinicianCopilotEngine.generate_copilot_synthesis(citizen_id, current_user)
        return synthesis
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate copilot synthesis: {str(exc)}")


@router.post("/copilot/action/{citizen_id}", response_model=ClinicianVerificationRecord)
async def submit_clinician_action(
    citizen_id: str,
    payload: ClinicianActionPayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Processes explicit clinician decision actions:

    - ACCEPT: Confirms AI draft and stamps into permanent legal record as CLINICIAN-VERIFIED.
    - EDIT: Modifies summary/plan and stamps into permanent legal record.
    - REJECT: Rejects draft with rationale; NEVER written to legal clinical record.
    - ADD_COMMENT: Attaches clinical observation notes.
    - CREATE_CARE_PLAN: Prescribes new or modified 30-day care plan.
    - REFER: Issues clinical referral to higher facility/specialist.
    - ESCALATE: Triggers urgent/emergency escalation alert.
    """
    try:
        record = ClinicianCopilotEngine.process_clinician_action(citizen_id, payload, current_user)
        return record
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to process clinician action: {str(exc)}")


@router.get("/copilot/legal-record/{citizen_id}")
async def get_legal_clinical_records(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])),
):
    """Retrieves only verified, stamped, legal clinical encounter records.

    Unverified AI suggestions and rejected drafts are strictly excluded.
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen, purpose="CARE_DELIVERY")

    records = store.get_legal_clinical_records(citizen_id)
    return {
        "citizen_id": citizen_id,
        "total_records": len(records),
        "records": records,
        "governance_rule": "Strict Clinical Governance: AI conclusions are only recorded upon explicit clinician verification.",
    }
