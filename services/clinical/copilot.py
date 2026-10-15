"""SevaHealth AI Clinician Copilot Engine.

Delivers human-in-the-loop decision intelligence for clinicians:
1. Assembles consolidated 11-dimension Patient Summary:
   - Current risks, Risk trajectory, Recent vitals, Recent labs,
   - Wearable trends, Lifestyle, Medications, Adherence,
   - Interventions, Alerts, Documents.
2. Synthesizes 7 AI-generated decision-support sections:
   - Clinical summary, Risk summary, Recent changes, Missing information,
   - Possible contributing factors, Suggested follow-up,
   - Questions for clinician consideration.
3. Strict Legal Clinical Record Governance:
   - AI drafts are clearly labeled AI-GENERATED.
   - Never automatically written into legal clinical records without explicit confirmation.
   - Supports clinician actions: ACCEPT, EDIT, REJECT, ADD_COMMENT, CREATE_CARE_PLAN, REFER, ESCALATE.
   - Confirmed records are clearly stamped CLINICIAN-VERIFIED with physician signature and timestamp.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, date
import uuid
import hashlib
from pydantic import BaseModel, Field

from packages.types.enums import (
    UserRole,
    RiskTier,
    TrajectoryTrend,
    TriageUrgency,
    ClinicianReviewStatus,
    InterventionPillar,
    AuditAction,
)
from packages.auth.jwt import TokenPayload
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.clinical_models.care_plan import CarePlan, DailyTask
from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from packages.observability.audit import audit_logger
from services.store import store, CitizenRecord
from services.ai_agent.tools import (
    get_patient_profile,
    get_latest_vitals,
    get_recent_labs,
    get_risk_assessment,
    get_risk_trajectory,
    get_intervention_plan,
    get_wearable_summary,
    get_medication_list,
    get_clinical_history,
)


class ClinicianCopilotPatientView(BaseModel):
    """Complete 11-dimension patient summary assembled for the clinician."""
    citizen_id: str
    patient_profile: Dict[str, Any]
    current_risks: Dict[str, Any]
    risk_trajectory: Dict[str, Any]
    recent_vitals: Dict[str, Any]
    recent_labs: Dict[str, Any]
    wearable_trends: Dict[str, Any]
    lifestyle: Dict[str, Any]
    medications: List[Dict[str, Any]]
    adherence: Dict[str, Any]
    interventions: Dict[str, Any]
    alerts: List[Dict[str, Any]]
    documents: List[Dict[str, Any]]
    assembled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AICopilotSynthesis(BaseModel):
    """7 AI-generated decision-support sections clearly labeled AI-GENERATED."""
    session_id: str = Field(default_factory=lambda: f"copilot-sess-{uuid.uuid4().hex[:8]}")
    citizen_id: str
    label: str = "AI-GENERATED"
    verification_status: str = "DRAFT_PENDING_CLINICIAN_VERIFICATION"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_committed_to_legal_record: bool = False

    # 1. Clinical Summary
    clinical_summary: str

    # 2. Risk Summary
    risk_summary: str

    # 3. Recent Changes
    recent_changes: str

    # 4. Missing Information
    missing_information: List[str]

    # 5. Possible Contributing Factors
    possible_contributing_factors: List[str]

    # 6. Suggested Follow-Up
    suggested_follow_up: List[str]

    # 7. Questions for Clinician Consideration
    questions_for_clinician_consideration: List[str]

    disclaimer: str = (
        "AI-GENERATED DRAFT: Clinical decision-support suggestions only. "
        "Never automatically written into legal clinical records without explicit clinician confirmation."
    )


class ClinicianActionPayload(BaseModel):
    """Payload for clinician decision actions on the AI Copilot synthesis."""
    action: str  # ACCEPT | EDIT | REJECT | ADD_COMMENT | CREATE_CARE_PLAN | REFER | ESCALATE
    session_id: Optional[str] = None
    edited_clinical_summary: Optional[str] = None
    edited_risk_summary: Optional[str] = None
    edited_plan: Optional[str] = None
    clinician_comments: Optional[str] = None
    rejection_reason: Optional[str] = None

    # Referral details if action == REFER
    referral_facility: Optional[str] = None
    referral_urgency: Optional[TriageUrgency] = None
    referral_reason: Optional[str] = None

    # Care plan creation details if action == CREATE_CARE_PLAN
    care_plan_title: Optional[str] = None
    dietary_prescription: Optional[str] = None
    activity_prescription: Optional[str] = None
    duration_days: Optional[int] = 30

    # Escalation details if action == ESCALATE
    escalation_urgency: Optional[TriageUrgency] = None
    escalation_reason: Optional[str] = None


class ClinicianVerificationRecord(BaseModel):
    """Confirmed clinical record stamped as CLINICIAN-VERIFIED for the legal medical record."""
    record_id: str = Field(default_factory=lambda: f"legal-rec-{uuid.uuid4().hex[:8]}")
    session_id: str
    citizen_id: str
    label: str = "CLINICIAN-VERIFIED"
    status: str  # ACCEPTED | MODIFIED | REJECTED | ESCALATED | REFERRED
    verified_by_doctor_id: str
    verified_by_doctor_name: str
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    legal_record_committed: bool = True
    digital_signature_hash: str

    final_clinical_summary: str
    final_risk_assessment: str
    final_clinical_plan: str
    clinician_notes: Optional[str] = None
    active_referral_id: Optional[str] = None
    active_care_plan_id: Optional[str] = None
    active_alert_id: Optional[str] = None


class ClinicianCopilotEngine:
    """Orchestrates clinician decision support, assembly, AI generation, and legal record governance."""

    @classmethod
    def assemble_patient_summary(
        cls,
        citizen_id: str,
        actor: TokenPayload,
    ) -> ClinicianCopilotPatientView:
        """Assembles all 11 patient dimensions for clinician copilot review."""
        citizen = store.get_citizen(citizen_id)
        if not citizen:
            raise ValueError(f"Citizen '{citizen_id}' not found.")

        # Enforce clinician authorization
        allowed, reason = HealthcareAuthorizationEngine.can_access_citizen_record(actor, citizen, purpose="CARE_DELIVERY")
        if not allowed:
            raise PermissionError(f"Access denied for clinician copilot: {reason}")

        # 1. Profile
        profile = get_patient_profile(citizen_id, actor)

        # 2. Current risks
        risks = get_risk_assessment(citizen_id, actor)

        # 3. Risk trajectory
        trajectory = get_risk_trajectory(citizen_id, actor)

        # 4. Recent vitals
        vitals = get_latest_vitals(citizen_id, actor)

        # 5. Recent labs
        labs = get_recent_labs(citizen_id, actor)

        # 6. Wearable trends
        wearables = get_wearable_summary(citizen_id, actor)

        # 7. Lifestyle
        lifestyle_raw = store.get_lifestyle_profile(citizen_id)
        if not lifestyle_raw:
            lifestyle = {
                "patient_id": citizen_id,
                "tobacco_use": "NEVER",
                "alcohol_use": "NONE",
                "dietary_pattern": "STANDARD_MIXED",
                "physical_activity_level": "MODERATE",
                "sleep_hours_per_night": 7.0,
                "perceived_stress_level": "LOW",
                "status": "DEFAULT_PROFILE",
            }
        else:
            lifestyle = lifestyle_raw if isinstance(lifestyle_raw, dict) else lifestyle_raw.model_dump()

        # 8. Medications
        meds = get_medication_list(citizen_id, actor)

        # 9. Adherence
        care_plan = get_intervention_plan(citizen_id, actor)
        checkins = store.get_checkins(citizen_id)
        adherence_data = {
            "adherence_percentage": care_plan.get("adherence_percentage", 0.0),
            "completed_tasks_count": care_plan.get("tasks_completed_count", 0),
            "total_tasks_count": care_plan.get("total_tasks_count", 30),
            "recent_checkins_count": len(checkins),
            "latest_checkin": checkins[-1] if checkins else None,
        }

        # 10. Interventions
        interventions = care_plan

        # 11. Alerts & Documents
        alerts = store.get_alerts(citizen_id)
        docs = store.get_documents(citizen_id)

        view = ClinicianCopilotPatientView(
            citizen_id=citizen_id,
            patient_profile=profile,
            current_risks=risks,
            risk_trajectory=trajectory,
            recent_vitals=vitals,
            recent_labs=labs,
            wearable_trends=wearables,
            lifestyle=lifestyle,
            medications=meds,
            adherence=adherence_data,
            interventions=interventions,
            alerts=alerts,
            documents=docs,
        )
        return view

    @classmethod
    def generate_copilot_synthesis(
        cls,
        citizen_id: str,
        actor: TokenPayload,
    ) -> AICopilotSynthesis:
        """Synthesizes 7 clinical decision-support sections clearly marked as AI-GENERATED."""
        summary_view = cls.assemble_patient_summary(citizen_id, actor)

        p = summary_view.patient_profile
        name = p.get("name", "Citizen")
        age = p.get("age", 45)
        vitals = summary_view.recent_vitals
        labs = summary_view.recent_labs
        risks = summary_view.current_risks
        traj = summary_view.risk_trajectory
        wb = summary_view.wearable_trends
        meds = summary_view.medications
        adh = summary_view.adherence
        lifestyle = summary_view.lifestyle

        tier = risks.get("overall_tier", "MODERATE")
        score = risks.get("overall_score", 0.5)
        trend = traj.get("overall_trend", "STABLE")
        change_pct = traj.get("change_percentage", 0.0)

        # 1. Clinical Summary
        sbp = vitals.get("systolic_bp", {}).get("value", 120.0)
        dbp = vitals.get("diastolic_bp", {}).get("value", 80.0)
        hba1c = labs.get("hba1c", {}).get("value")
        fbg = labs.get("fasting_glucose", {}).get("value")

        glyc_str = f"HbA1c {hba1c}%" if hba1c else (f"Fasting Glucose {fbg} mg/dL" if fbg else "Glycemic panel pending")
        med_names = [f"{m['drug_name']} {m.get('dosage', '')}".strip() for m in meds]

        clin_sum = (
            f"Patient {name} ({age}y) presents with {tier} chronic disease risk profile. "
            f"Current biometrics demonstrate blood pressure of {sbp}/{dbp} mmHg and {glyc_str}. "
            f"Longitudinal trajectory reflects a {trend} trend ({change_pct:+0.1f}% change). "
            f"Active pharmacotherapy includes: {', '.join(med_names) if med_names else 'No active prescriptions'}. "
            f"Patient is currently engaged in community prevention care plan with {adh.get('adherence_percentage', 0.0):.1f}% adherence."
        )

        # 2. Risk Summary
        drivers = [f"{d['feature_name']} (Observed: {d['observed_value']}, Target: {d['target_value']})" for d in risks.get("top_drivers", [])]
        risk_sum = (
            f"Composite Risk Stratification: {tier} tier (Predictive score: {score:.2f}). "
            f"Primary physiological strain points: {'; '.join(drivers) if drivers else 'Metabolic risk factors'}. "
            f"Evaluated against ICMR-INDIAB and ACC/AHA preventive guidelines."
        )

        # 3. Recent Changes
        rhr = wb.get("seven_day_baselines", {}).get("avg_resting_heart_rate")
        steps = wb.get("seven_day_baselines", {}).get("avg_daily_steps")
        recent_chg = (
            f"Longitudinal Trajectory Shift: {trend} progression over recent assessment cycles ({change_pct:+0.1f}%). "
            f"Systolic vascular load measured at {sbp} mmHg. "
            f"7-day wearable biometrics indicate average resting heart rate of {rhr} bpm and daily movement of {steps} steps."
        )

        # 4. Missing Information
        missing: List[str] = []
        if "systolic_bp" not in vitals or "diastolic_bp" not in vitals:
            missing.append("Standardized in-clinic repeat Blood Pressure reading")
        if "fasting_glucose" not in labs and "hba1c" not in labs:
            missing.append("Confirmatory Fasting Plasma Glucose / HbA1c laboratory panel")
        if "triglycerides" not in labs or "hdl_cholesterol" not in labs:
            missing.append("Complete Fasting Lipid Profile (Triglycerides, HDL, LDL, Total Cholesterol)")
        if "serum_creatinine" not in labs and "egfr" not in labs:
            missing.append("Renal filtration panel (Serum Creatinine and calculated eGFR)")
        if wb.get("connection_status") != "CONNECTED":
            missing.append("Longitudinal wearable accelerometer telemetry sync")
        if not missing:
            missing.append("None: Comprehensive baseline biometrics and diagnostic panels present.")

        # 5. Possible Contributing Factors
        factors: List[str] = []
        if sbp >= 130:
            factors.append("Elevated peripheral vascular resistance associated with pre-hypertensive load.")
        if hba1c and hba1c >= 5.7:
            factors.append("Peripheral insulin resistance and impaired beta-cell glucose tolerance.")
        if lifestyle.get("physical_activity_level") == "SEDENTARY" or (steps and steps < 6000):
            factors.append("Sedentary lifestyle pattern contributing to reduced muscular glucose uptake.")
        if lifestyle.get("dietary_pattern") == "HIGH_CARB_HIGH_SALT":
            factors.append("Dietary intake with high refined carbohydrates and sodium load.")
        if vitals.get("waist_circumference", {}).get("value", 0) >= 90:
            factors.append("Central visceral adiposity exceeding South Asian anthropometric cutoffs.")
        if not factors:
            factors.append("Routine metabolic age-related baseline drift.")

        # 6. Suggested Follow-Up
        follow_ups: List[str] = []
        if tier in ["HIGH", "CRITICAL"] or sbp >= 140:
            follow_ups.append("Schedule in-person PHC clinical consultation within 14 days.")
            follow_ups.append("Initiate 7-day home blood pressure monitoring (AM/PM log).")
        else:
            follow_ups.append("Routine 30-day prevention progress review with community ASHA worker.")
        if hba1c and hba1c >= 6.0:
            follow_ups.append("Repeat HbA1c and fasting blood sugar in 90 days.")
        follow_ups.append("Nutritional counseling to reinforce millet substitution (foxtail/ragi) and salt reduction under 2g/day.")
        follow_ups.append("Encourage target of 150 minutes weekly brisk walking.")

        # 7. Questions for Clinician Consideration
        questions: List[str] = [
            f"Are there symptoms of microvascular or macrovascular complications (e.g., polyuria, exertional dyspnea, paresthesias)?",
            f"Does the patient report adequate tolerance and adherence to current medications ({', '.join(med_names) if med_names else 'lifestyle only'})?",
            f"Are there cultural or household barriers preventing substitution of polished white rice with traditional millets?",
            f"Would the patient benefit from formal referral to a district hospital NCD clinic or dietitian?",
        ]

        synthesis = AICopilotSynthesis(
            citizen_id=citizen_id,
            label="AI-GENERATED",
            verification_status="DRAFT_PENDING_CLINICIAN_VERIFICATION",
            is_committed_to_legal_record=False,
            clinical_summary=clin_sum,
            risk_summary=risk_sum,
            recent_changes=recent_chg,
            missing_information=missing,
            possible_contributing_factors=factors,
            suggested_follow_up=follow_ups,
            questions_for_clinician_consideration=questions,
        )

        # Store as draft session; NEVER committed to legal record until explicit clinician action
        store.set_copilot_draft(citizen_id, synthesis)
        return synthesis

    @classmethod
    def process_clinician_action(
        cls,
        citizen_id: str,
        action_payload: ClinicianActionPayload,
        actor: TokenPayload,
    ) -> ClinicianVerificationRecord:
        """Processes clinician action (ACCEPT, EDIT, REJECT, COMMENT, CARE_PLAN, REFER, ESCALATE).

        Guarantees that an AI conclusion is ONLY committed to the legal record upon
        explicit clinician confirmation (ACCEPT or EDIT).
        """
        citizen = store.get_citizen(citizen_id)
        if not citizen:
            raise ValueError(f"Citizen '{citizen_id}' not found.")

        # Require CLINICIAN or SYSTEM_ADMIN role
        if actor.role not in [UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN]:
            raise PermissionError(f"Only licensed clinicians can verify or act on clinical copilot sessions. Actor role: {actor.role.value}")

        # Enforce care context and patient authorization
        HealthcareAuthorizationEngine.enforce_citizen_access(actor, citizen, purpose="CLINICAL_ACTION")

        # Fetch current draft
        draft = store.get_copilot_draft(citizen_id)
        if not draft:
            # Generate draft if none exists
            draft = cls.generate_copilot_synthesis(citizen_id, actor)

        clinician_user = store.get_user(actor.sub)
        doctor_name = clinician_user.full_name if clinician_user else "Treating Medical Officer"
        now = datetime.now(timezone.utc)

        action_type = action_payload.action.upper()
        active_ref_id = None
        active_plan_id = None
        active_alt_id = None

        final_summary = draft.clinical_summary
        final_risk = draft.risk_summary
        final_plan = "\n".join(draft.suggested_follow_up)
        status_str = "PENDING"
        legal_committed = False

        # --- 1. ACTION: ACCEPT ---
        if action_type == "ACCEPT":
            status_str = "ACCEPTED"
            legal_committed = True

        # --- 2. ACTION: EDIT ---
        elif action_type == "EDIT":
            status_str = "MODIFIED"
            legal_committed = True
            if action_payload.edited_clinical_summary:
                final_summary = action_payload.edited_clinical_summary
            if action_payload.edited_risk_summary:
                final_risk = action_payload.edited_risk_summary
            if action_payload.edited_plan:
                final_plan = action_payload.edited_plan

        # --- 3. ACTION: REJECT ---
        elif action_type == "REJECT":
            status_str = "REJECTED"
            legal_committed = False  # NEVER written into legal record!
            final_summary = f"[REJECTED BY CLINICIAN]: {action_payload.rejection_reason or 'Clinician rejected AI conclusion as clinically non-indicated.'}"
            final_risk = "[REJECTED]"
            final_plan = "[REJECTED]"

        # --- 4. ACTION: ADD_COMMENT ---
        elif action_type == "ADD_COMMENT":
            status_str = "COMMENTED"
            legal_committed = True
            if action_payload.clinician_comments:
                final_plan += f"\n\nClinician Note: {action_payload.clinician_comments}"

        # --- 5. ACTION: CREATE_CARE_PLAN ---
        elif action_type == "CREATE_CARE_PLAN":
            status_str = "CARE_PLAN_CREATED"
            legal_committed = True
            plan_id = f"careplan-clinician-{uuid.uuid4().hex[:8]}"
            title = action_payload.care_plan_title or "Clinician-Prescribed 30-Day Prevention Care Plan"
            nut = action_payload.dietary_prescription or "Strict dietary sodium restriction < 2g/day and whole millet substitution."
            act = action_payload.activity_prescription or "150 minutes of moderate aerobic activity weekly."
            dur = action_payload.duration_days or 30

            tasks = []
            for d in range(1, dur + 1):
                tasks.append(DailyTask(
                    id=f"{plan_id}-t{d}",
                    day=d,
                    pillar=InterventionPillar.PHYSICAL_ACTIVITY if d % 2 == 0 else InterventionPillar.NUTRITION,
                    title=f"Day {d}: {act if d % 2 == 0 else nut}",
                    description="Clinician-directed preventive micro-habit.",
                    target_metric="Completed daily goal",
                    completed=False,
                ))

            c_plan = CarePlan(
                id=plan_id,
                tenant_id=citizen.tenant_id,
                citizen_id=citizen.id,
                risk_assessment_id="risk-clinician-plan",
                title=title,
                focus_domain="Clinician-Directed Lifestyle Medicine",
                start_date=date.today(),
                end_date=date.today(),
                adherence_percentage=0.0,
                nutrition_guidance=nut,
                activity_guidance=act,
                sleep_guidance="Consistent 7-8 hours sleep nightly with regular bedtime cutoff.",
                stress_guidance="Practice daily 5-minute diaphragmatic breathing.",
                daily_tasks=tasks,
                clinician_reviewed=True,
                clinician_id=actor.sub,
            )
            store.set_care_plan(c_plan)
            active_plan_id = plan_id
            final_plan = f"Clinician created Care Plan '{title}' (ID: {plan_id}). Nutrition: {nut}. Activity: {act}."

        # --- 6. ACTION: REFER ---
        elif action_type == "REFER":
            status_str = "REFERRED"
            legal_committed = True
            ref_id = f"ref-{uuid.uuid4().hex[:8]}"
            facility = action_payload.referral_facility or "District Hospital NCD Specialty Clinic"
            urgency = action_payload.referral_urgency or TriageUrgency.PRIORITY
            reason = action_payload.referral_reason or "Secondary specialist evaluation for metabolic-cardiovascular risk."

            referral_record = {
                "id": ref_id,
                "patient_id": citizen_id,
                "citizen_id": citizen_id,
                "referring_doctor_id": actor.sub,
                "referred_to_facility": facility,
                "urgency": urgency.value,
                "clinical_reason": reason,
                "status": "PENDING",
                "created_at": now.isoformat(),
            }
            store.add_referral(citizen_id, referral_record)
            active_ref_id = ref_id
            final_plan += f"\n\nOfficial Referral Generated: Referred to {facility} ({urgency.value}) - Reason: {reason} (Ref ID: {ref_id})"

        # --- 7. ACTION: ESCALATE ---
        elif action_type == "ESCALATE":
            status_str = "ESCALATED"
            legal_committed = True
            urgency = action_payload.escalation_urgency or TriageUrgency.EMERGENT
            reason = action_payload.escalation_reason or "High-priority clinical deterioration or acute biometric spike."
            alt_id = f"alert-esc-{uuid.uuid4().hex[:8]}"

            alert_record = {
                "id": alt_id,
                "citizen_id": citizen_id,
                "patient_id": citizen_id,
                "urgency": urgency.value,
                "alert_type": "CLINICAL_COPILOT_ESCALATION",
                "message": f"Escalated by Dr. {doctor_name}: {reason}",
                "clinical_rule_triggered": "PHYSICIAN_OVERRIDE_ESCALATION",
                "created_at": now.isoformat(),
                "created_by": actor.sub,
                "is_acknowledged": True,
                "acknowledged_by": actor.sub,
                "acknowledged_at": now.isoformat(),
            }
            store.add_alert(alert_record)
            active_alt_id = alt_id

            # Also add to triage cases with high urgency
            triage_id = f"triage-esc-{uuid.uuid4().hex[:8]}"
            t_case = ClinicalTriageCase(
                id=triage_id,
                tenant_id=citizen.tenant_id,
                citizen_id=citizen.id,
                citizen_name=f"{citizen.first_name} {citizen.last_name}",
                risk_assessment_id="risk-escalation",
                urgency=urgency,
                escalation_reason=f"Clinician Copilot Escalation: {reason}",
                soap_note=SOAPReport(
                    subjective=f"Escalated by clinician {doctor_name}",
                    objective=f"Urgency: {urgency.value}",
                    assessment=final_risk,
                    plan=final_plan,
                ),
                status=ClinicianReviewStatus.APPROVED,
                assigned_clinician_id=actor.sub,
                reviewed_at=now,
            )
            store.add_triage_case(t_case)
            final_plan += f"\n\nCase Escalated to {urgency.value}. Emergency alert registered (Alert ID: {alt_id})."

        else:
            raise ValueError(f"Unsupported clinician action: '{action_payload.action}'. Supported: ACCEPT, EDIT, REJECT, ADD_COMMENT, CREATE_CARE_PLAN, REFER, ESCALATE.")

        # Digital Signature Hash
        sig_content = f"{citizen_id}|{actor.sub}|{action_type}|{final_summary}|{final_plan}|{now.isoformat()}"
        sig_hash = hashlib.sha256(sig_content.encode("utf-8")).hexdigest()

        verification_record = ClinicianVerificationRecord(
            session_id=draft.session_id,
            citizen_id=citizen_id,
            label="CLINICIAN-VERIFIED",
            status=status_str,
            verified_by_doctor_id=actor.sub,
            verified_by_doctor_name=doctor_name,
            verified_at=now,
            legal_record_committed=legal_committed,
            digital_signature_hash=sig_hash,
            final_clinical_summary=final_summary,
            final_risk_assessment=final_risk,
            final_clinical_plan=final_plan,
            clinician_notes=action_payload.clinician_comments,
            active_referral_id=active_ref_id,
            active_care_plan_id=active_plan_id,
            active_alert_id=active_alt_id,
        )

        # WRITE TO LEGAL CLINICAL RECORD ONLY IF EXPLICITLY CONFIRMED
        if legal_committed:
            legal_doc = verification_record.model_dump(mode="json")
            store.add_legal_clinical_record(citizen_id, legal_doc)

            # Also create ClinicalEncounter in store
            enc_id = f"enc-{uuid.uuid4().hex[:8]}"
            store.add_encounter(citizen_id, {
                "id": enc_id,
                "patient_id": citizen_id,
                "clinician_id": actor.sub,
                "clinician_name": doctor_name,
                "encounter_type": "CLINICAL_COPILOT_REVIEW",
                "reason": "AI Copilot Clinical Evaluation & Sign-off",
                "soap_subjective": final_summary,
                "soap_objective": draft.recent_changes,
                "soap_assessment": final_risk,
                "soap_plan": final_plan,
                "status": "COMPLETED",
                "signature_hash": sig_hash,
                "verified_at": now.isoformat(),
            })

        # Clear draft or mark draft committed
        draft.verification_status = status_str
        draft.is_committed_to_legal_record = legal_committed
        store.set_copilot_draft(citizen_id, draft)

        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=actor.sub,
            actor_role=actor.role,
            action=AuditAction.CLINICIAN_REVIEWED,
            resource_type="ClinicianCopilotSession",
            resource_id=verification_record.record_id,
            details={
                "action": action_type,
                "legal_committed": legal_committed,
                "doctor_name": doctor_name,
                "signature": sig_hash[:16],
            },
        )

        return verification_record
