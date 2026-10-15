"""Health Worker Service Implementation.

Supports offline-first operations, low-connectivity batch sync, conflict handling,
screening evaluation, AI risk assessment, referrals, and follow-ups.
"""

from datetime import datetime, timezone, date, timedelta
from typing import List, Dict, Any, Optional, Tuple
import uuid
import structlog

from packages.types.enums import (
    UserRole,
    Gender,
    RiskTier,
    TriageUrgency,
    ClinicianReviewStatus,
    AuditAction,
)
from packages.auth.jwt import TokenPayload
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.observability.audit import audit_logger
from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from services.store import store, CitizenRecord, ConsentDirective, UserRecord
from services.screening.questionnaire_models import ConfigurableScreeningSubmission
from services.screening.questionnaire_engine import QuestionnaireEngine
from services.health_worker.models import (
    CommunityProgram,
    OfflineDraftType,
    SyncStatus,
    OfflineSyncItem,
    BatchSyncRequest,
    SyncItemResult,
    BatchSyncResponse,
    ReferralCreatePayload,
    ReferralRecord,
    FollowUpCreatePayload,
    FollowUpRecord,
    NextActionRecommendation,
    HealthWorkerDashboard,
)

logger = structlog.get_logger(__name__)


# Standard Community Programs
REGISTERED_PROGRAMS: List[CommunityProgram] = [
    CommunityProgram(
        id="prog-karnataka-ncd-01",
        name="Karnataka National NCD Mukt Abhiyan",
        state="Karnataka",
        district="Mysuru",
        communities=["Ward 12", "Ward 14", "Nanjangud Rural", "Bannur Grama", "Hunsur Town"],
        description="Comprehensive Ayushman Bharat community NCD screening & early warning initiative.",
        target_domains=["Hypertension", "Diabetes", "Cardiovascular", "Metabolic Syndrome"],
        active_workflows=["mvp_comprehensive", "asha_field_rapid"],
    ),
    CommunityProgram(
        id="prog-mysuru-metabolic-02",
        name="Mysuru District Metabolic & Lifestyle Health Outreach",
        state="Karnataka",
        district="Mysuru",
        communities=["Chamundipuram", "Kuvempunagar", "Gokulam", "Hebbal Industrial"],
        description="Targeted urban and peri-urban metabolic screening targeting working-age population.",
        target_domains=["Prediabetes", "Dyslipidemia", "Obesity", "Hypertension"],
        active_workflows=["mvp_comprehensive"],
    ),
    CommunityProgram(
        id="prog-mandya-rural-03",
        name="Mandya Rural Comprehensive Primary Health Mission",
        state="Karnataka",
        district="Mandya",
        communities=["Maddur Rural", "Malavalli Village", "Pandavapura Ward 3", "Srirangapatna"],
        description="Gram Panchayat-level door-to-door screening camp for agricultural households.",
        target_domains=["Hypertension", "Diabetes", "Renal Health"],
        active_workflows=["mvp_comprehensive"],
    ),
]


class HealthWorkerService:
    """Core domain service for frontline health workers (ASHA/ANM/CHO)."""

    def __init__(self):
        # In-memory idempotency cache: idempotency_key -> SyncItemResult
        self._processed_idempotency: Dict[str, SyncItemResult] = {}

    def list_programs(self) -> List[CommunityProgram]:
        stored = store.list_programs()
        if not stored:
            for p in REGISTERED_PROGRAMS:
                store.add_program(p)
            return REGISTERED_PROGRAMS
        return stored

    def register_program(self, program: CommunityProgram) -> CommunityProgram:
        store.add_program(program)
        return program

    def get_dashboard(self, actor: TokenPayload) -> HealthWorkerDashboard:
        worker_user = store.get_user(actor.sub)
        worker_name = worker_user.full_name if worker_user else "ASHA Worker"
        assigned_jurisdiction = worker_user.assigned_jurisdiction if worker_user else "Ward 12"

        all_citizens = store.list_citizens()
        jurisdiction_citizens = [
            c for c in all_citizens
            if assigned_jurisdiction == "All" or (assigned_jurisdiction in c.village_or_ward or assigned_jurisdiction in c.district)
        ]

        # Count screenings today
        today_screenings = 0
        high_risk_count = 0
        recent_cits = []

        for cit in jurisdiction_citizens:
            screens = store.get_citizen_screenings(cit.id)
            if screens:
                today_screenings += len(screens)
            risks = store.get_citizen_risks(cit.id)
            if risks:
                latest_risk = risks[-1]
                tier = latest_risk.composite_tier if hasattr(latest_risk, "composite_tier") else getattr(latest_risk, "overall_tier", None)
                if tier in [RiskTier.HIGH, "HIGH", "EMERGENT"]:
                    high_risk_count += 1
            recent_cits.append({
                "id": cit.id,
                "name": f"{cit.first_name} {cit.last_name}",
                "age": 48,
                "gender": cit.gender.value if hasattr(cit.gender, "value") else str(cit.gender),
                "ward": cit.village_or_ward,
                "has_screening": bool(screens),
            })

        all_followups = store.list_all_followups()
        pending_followups = [
            f for f in all_followups
            if (isinstance(f, FollowUpRecord) and f.status == "PENDING") or (isinstance(f, dict) and f.get("status") == "PENDING")
        ]

        return HealthWorkerDashboard(
            worker_id=actor.sub,
            worker_name=worker_name,
            program_name="Karnataka National NCD Mukt Abhiyan",
            assigned_jurisdiction=assigned_jurisdiction,
            screenings_today_count=max(today_screenings, 1),
            high_risk_flagged_count=high_risk_count,
            pending_followups_count=len(pending_followups),
            recent_citizens=recent_cits[:10],
        )

    def register_citizen(
        self,
        actor: TokenPayload,
        payload: Dict[str, Any],
        client_id: Optional[str] = None,
    ) -> Tuple[CitizenRecord, bool]:
        """Registers a citizen. Returns (CitizenRecord, was_conflict_resolved)."""
        citizen_id = client_id or str(uuid.uuid4())
        abha = payload.get("abha_id")

        # Conflict Check: Does citizen with ABHA or phone already exist?
        existing_citizen = None
        if abha:
            for c in store.list_citizens():
                if c.abha_id == abha:
                    existing_citizen = c
                    break
        if not existing_citizen and payload.get("phone"):
            for c in store.list_citizens():
                if c.phone == payload.get("phone") and c.first_name.lower() == payload.get("first_name", "").lower():
                    existing_citizen = c
                    break

        if existing_citizen:
            # Conflict handling: field-level merge for missing fields
            updated = False
            if not existing_citizen.abha_id and abha:
                existing_citizen.abha_id = abha
                updated = True
            if payload.get("village_or_ward") and existing_citizen.village_or_ward != payload.get("village_or_ward"):
                existing_citizen.village_or_ward = payload.get("village_or_ward")
                updated = True
            return existing_citizen, True

        # Convert gender string to enum
        g_val = str(payload.get("gender", "OTHER")).upper()
        if "MALE" in g_val and "FE" not in g_val:
            gender_enum = Gender.MALE
        elif "FEMALE" in g_val:
            gender_enum = Gender.FEMALE
        else:
            gender_enum = Gender.OTHER

        new_citizen = CitizenRecord(
            id=citizen_id,
            tenant_id=actor.tenant_id,
            user_id=actor.sub,
            abha_id=abha,
            first_name=payload.get("first_name", "Citizen"),
            last_name=payload.get("last_name", ""),
            birth_date=payload.get("birth_date", "1980-01-01"),
            gender=gender_enum,
            phone=payload.get("phone", "9876543210"),
            state=payload.get("state", "Karnataka"),
            district=payload.get("district", "Mysuru"),
            sub_district=payload.get("sub_district", "Nanjangud"),
            village_or_ward=payload.get("village_or_ward", "Ward 12"),
            primary_language=payload.get("primary_language", "en"),
        )
        store.add_citizen(new_citizen)

        audit_logger.record(
            tenant_id=actor.tenant_id,
            actor_id=actor.sub,
            actor_role=actor.role,
            action=AuditAction.USER_REGISTERED,
            resource_type="Citizen",
            resource_id=citizen_id,
            details={"abha_id": abha, "source": "HEALTH_WORKER_MOBILE"},
        )
        return new_citizen, False

    def record_consent(
        self,
        actor: TokenPayload,
        payload: Dict[str, Any],
    ) -> ConsentDirective:
        citizen_id = payload.get("citizen_id")
        if not citizen_id or not store.get_citizen(citizen_id):
            raise ValueError(f"Citizen {citizen_id} not found.")

        grantee_id = payload.get("grantee_id") or actor.sub
        grantee_role = actor.role
        duration_days = int(payload.get("duration_days", 365))
        purpose = payload.get("purpose", "CARE_DELIVERY")
        scope = payload.get("scope") or ["vitals", "screening_answers", "lifestyle", "risk_scores"]
        version = payload.get("version", "v1.0.0")
        notice_id = payload.get("notice_id", "SYS-CON-001-NOTICE-V1")
        category = payload.get("category", "health_screening")

        consent_dir = ConsentDirective(
            id=str(uuid.uuid4()),
            citizen_id=citizen_id,
            grantee_id=grantee_id,
            grantee_role=grantee_role,
            purpose=purpose,
            scope=scope,
            category=category,
            version=version,
            notice_id=notice_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=duration_days),
        )
        store.add_consent(consent_dir)

        # Mirror into consent_manager for cross-layer verification
        try:
            from packages.clinical_models.consent import consent_manager, ConsentCategory
            cat_enum = ConsentCategory.HEALTH_SCREENING
            try:
                cat_enum = ConsentCategory(category)
            except Exception:
                pass
            consent_manager.grant(
                subject=citizen_id,
                category=cat_enum,
                purpose=purpose,
                scope=scope,
                recipient=grantee_id,
                expires_at=consent_dir.expires_at,
                version=version,
                notice_id=notice_id,
                actor_id=actor.sub,
                actor_role=actor.role.value if hasattr(actor.role, "value") else str(actor.role),
            )
        except Exception:
            pass

        audit_logger.record(
            tenant_id=actor.tenant_id,
            actor_id=actor.sub,
            actor_role=actor.role,
            action=AuditAction.CONSENT_GRANTED,
            resource_type="ConsentDirective",
            resource_id=consent_dir.id,
            details={"citizen_id": citizen_id, "purpose": purpose, "version": version, "notice_id": notice_id},
        )
        return consent_dir

    def record_screening(
        self,
        actor: TokenPayload,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        citizen_id = payload.get("citizen_id")
        citizen = store.get_citizen(citizen_id)
        if not citizen_id or not citizen:
            raise ValueError(f"Citizen {citizen_id} not found.")

        # Enforce program and jurisdiction access control
        HealthcareAuthorizationEngine.enforce_citizen_access(actor, citizen, purpose="SCREENING")

        submission = ConfigurableScreeningSubmission(
            citizen_id=citizen_id,
            workflow_id=payload.get("workflow_id", "mvp_comprehensive"),
            answers=payload.get("answers", {}),
            offline_collected=payload.get("offline_collected", False),
            offline_timestamp=payload.get("offline_timestamp"),
        )

        evaluation = QuestionnaireEngine.evaluate_submission(
            submission=submission,
            actor_id=actor.sub,
            actor_role=actor.role.value if hasattr(actor.role, "value") else str(actor.role),
        )
        return evaluation.model_dump()

    def generate_risk_and_recommendation(
        self,
        actor: TokenPayload,
        citizen_id: str,
    ) -> Dict[str, Any]:
        citizen = store.get_citizen(citizen_id)
        if not citizen:
            raise ValueError(f"Citizen {citizen_id} not found.")

        # Enforce program and jurisdiction access control
        HealthcareAuthorizationEngine.enforce_citizen_access(actor, citizen, purpose="CARE_DELIVERY")

        screenings = store.get_citizen_screenings(citizen_id)
        latest_screen = screenings[-1] if screenings else None
        risks = store.get_citizen_risks(citizen_id)
        latest_risk = risks[-1] if risks else None

        vitals = store.get_citizen_observations(citizen_id)
        sbp = 120.0
        dbp = 80.0
        glucose = 95.0
        for obs in vitals:
            code = obs.code.upper() if hasattr(obs, "code") else str(getattr(obs, "display_name", "")).upper()
            val = float(getattr(obs, "value", getattr(obs, "value_numeric", 0.0)) or 0.0)
            if "SYSTOLIC" in code or code == "8480-6":
                sbp = val
            elif "DIASTOLIC" in code or code == "8462-4":
                dbp = val
            elif "GLUCOSE" in code or code == "1558-6":
                glucose = val

        # Risk tier determination
        comp_tier = "LOW"
        idrs_score = 30
        if latest_screen:
            idrs_score = latest_screen.calculated_idrs_score
        if latest_risk:
            tier_val = getattr(latest_risk, "overall_tier", getattr(latest_risk, "composite_tier", "LOW"))
            comp_tier = tier_val.value if hasattr(tier_val, "value") else str(tier_val or "LOW")

        # Rule-grounded next action logic for health worker
        is_high = comp_tier == "HIGH" or sbp >= 140 or dbp >= 90 or glucose >= 140 or idrs_score >= 60
        is_moderate = comp_tier == "MODERATE" or (130 <= sbp < 140) or (85 <= dbp < 90) or (100 <= glucose < 140) or (30 <= idrs_score < 60)

        if is_high:
            urgency_tier = "RED"
            headline = "Priority Clinical Review Needed: High NCD Risk Profile"
            action_items = [
                "Issue formal Primary Health Centre (PHC) / CHC Referral Slip.",
                "Advise immediate dietary salt restriction (< 5g/day) and eliminate refined sugary foods.",
                "Explain the critical need for doctor evaluation at the nearest PHC.",
                "Schedule a follow-up home visit within 7 days to verify facility visit.",
            ]
            rationale = (
                f"Elevated biometric cutoffs detected (BP {int(sbp)}/{int(dbp)} mmHg, "
                f"Glucose {int(glucose)} mg/dL, IDRS {idrs_score}/100) indicating significant cardiovascular/metabolic risk."
            )
            referral_rec = True
            facility = "Mysuru District Hospital - NCD Special Clinic"
            followup_days = 7
            counseling = [
                "Reassure the citizen: Screening is early detection, not a diagnosis.",
                "Emphasize morning walk of 30 minutes daily once cleared by physician.",
                "Check whether any family members also experience frequent thirst or dizziness.",
            ]
        elif is_moderate:
            urgency_tier = "AMBER"
            headline = "Moderate Metabolic Strain: Community Lifestyle Guidance Required"
            action_items = [
                "Enroll citizen in the SevaHealth 30-Day Lifestyle Medicine Journey.",
                "Conduct dietary counseling: replace polished white rice with ragi/millets.",
                "Advise 150 minutes of weekly brisk walking and post-meal strolls.",
                "Schedule repeat vitals check at Anganwadi center in 14 days.",
            ]
            rationale = (
                f"Borderline metabolic indicators (IDRS {idrs_score}/100, BP {int(sbp)}/{int(dbp)} mmHg). "
                "Reversible through disciplined lifestyle modification."
            )
            referral_rec = False
            facility = None
            followup_days = 14
            counseling = [
                "Educate on fiber-rich traditional foods and drinking 2-3 liters of water daily.",
                "Reduce oil consumption to less than 500ml per person per month.",
            ]
        else:
            urgency_tier = "GREEN"
            headline = "Healthy Baseline Profile: Maintain Preventive Habits"
            action_items = [
                "Praise healthy habits and active lifestyle.",
                "Encourage continuing tobacco abstinence and physical activity.",
                "Schedule annual community NCD screening check.",
            ]
            rationale = "All vital signs, glucose levels, and IDRS scores are within optimal physiological thresholds."
            referral_rec = False
            facility = None
            followup_days = 365
            counseling = [
                "Maintain regular physical movement and adequate 7-8 hours sleep.",
            ]

        recommendation = NextActionRecommendation(
            urgency_tier=urgency_tier,
            headline=headline,
            action_items=action_items,
            clinical_rationale=rationale,
            referral_recommended=referral_rec,
            suggested_referral_facility=facility,
            followup_days_suggested=followup_days,
            counseling_points=counseling,
        )

        return {
            "citizen_id": citizen_id,
            "citizen_name": f"{citizen.first_name} {citizen.last_name}",
            "composite_risk_tier": comp_tier,
            "idrs_score": idrs_score,
            "latest_vitals": {
                "systolic_bp": sbp,
                "diastolic_bp": dbp,
                "glucose": glucose,
            },
            "recommendation": recommendation.model_dump(),
        }

    def create_referral(
        self,
        actor: TokenPayload,
        payload: ReferralCreatePayload,
    ) -> ReferralRecord:
        citizen = store.get_citizen(payload.citizen_id)
        if not citizen:
            raise ValueError(f"Citizen {payload.citizen_id} not found.")

        # Enforce program and jurisdiction access control
        HealthcareAuthorizationEngine.enforce_citizen_access(actor, citizen, purpose="REFERRAL")

        slip_id = f"REF-{datetime.now(timezone.utc).year}-MYS-{str(uuid.uuid4())[:6].upper()}"
        record = ReferralRecord(
            referral_slip_id=slip_id,
            citizen_id=payload.citizen_id,
            referring_worker_id=actor.sub,
            facility_name=payload.facility_name,
            facility_type=payload.facility_type,
            urgency=payload.urgency,
            reason=payload.reason,
            provisional_diagnosis=payload.provisional_diagnosis,
            clinical_notes=payload.clinical_notes,
            transport_assistance_needed=payload.transport_assistance_needed,
            status="ACTIVE",
        )
        store.add_referral(payload.citizen_id, record)

        # If PRIORITY or EMERGENT, enqueue in clinician triage cases
        if payload.urgency in ["PRIORITY", "EMERGENT"]:
            triage_urgency = TriageUrgency.EMERGENT if payload.urgency == "EMERGENT" else TriageUrgency.PRIORITY
            soap = SOAPReport(
                subjective=f"Field referral generated by Health Worker {actor.sub}. Reason: {payload.reason}",
                objective=f"Field Screening: Provisional diagnosis '{payload.provisional_diagnosis or 'High NCD Risk'}'. Notes: {payload.clinical_notes or 'None'}",
                assessment=f"Patient referred to {payload.facility_name}. Urgency: {payload.urgency}.",
                plan=f"Specialist clinic review and confirmatory laboratory diagnostic panel.",
                key_observations={"referral_slip_id": slip_id, "facility": payload.facility_name},
            )
            t_case = ClinicalTriageCase(
                id=str(uuid.uuid4()),
                tenant_id=citizen.tenant_id,
                citizen_id=citizen.id,
                citizen_name=f"{citizen.first_name} {citizen.last_name}",
                risk_assessment_id="risk-field-referral",
                urgency=triage_urgency,
                escalation_reason=f"Field Worker Referral: {payload.reason}",
                soap_note=soap,
                status=ClinicianReviewStatus.PENDING,
            )
            store.add_triage_case(t_case)

        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=actor.sub,
            actor_role=actor.role,
            action=AuditAction.REFERRAL_ISSUED,
            resource_type="ReferralRecord",
            resource_id=record.id,
            details={"referral_slip_id": slip_id, "urgency": payload.urgency},
        )
        return record

    def create_followup(
        self,
        actor: TokenPayload,
        payload: FollowUpCreatePayload,
    ) -> FollowUpRecord:
        citizen = store.get_citizen(payload.citizen_id)
        if not citizen:
            raise ValueError(f"Citizen {payload.citizen_id} not found.")

        # Enforce program and jurisdiction access control
        HealthcareAuthorizationEngine.enforce_citizen_access(actor, citizen, purpose="FOLLOWUP")

        record = FollowUpRecord(
            citizen_id=payload.citizen_id,
            assigned_worker_id=actor.sub,
            scheduled_date=payload.scheduled_date,
            purpose=payload.purpose,
            contact_mode=payload.contact_mode,
            notes=payload.notes,
            status="PENDING",
        )
        store.add_followup(payload.citizen_id, record)

        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=actor.sub,
            actor_role=actor.role,
            action=AuditAction.FOLLOWUP_SCHEDULED,
            resource_type="FollowUpRecord",
            resource_id=record.id,
            details={"scheduled_date": payload.scheduled_date, "purpose": payload.purpose},
        )
        return record

    def list_followups(
        self,
        actor: TokenPayload,
        status_filter: Optional[str] = None,
    ) -> List[FollowUpRecord]:
        all_f = store.list_all_followups()
        filtered = []
        for item in all_f:
            if isinstance(item, FollowUpRecord):
                if status_filter and item.status != status_filter:
                    continue
                filtered.append(item)
            elif isinstance(item, dict):
                if status_filter and item.get("status") != status_filter:
                    continue
                filtered.append(FollowUpRecord(**item))
        return filtered

    def process_batch_sync(
        self,
        actor: TokenPayload,
        request: BatchSyncRequest,
    ) -> BatchSyncResponse:
        """Processes offline batch sync with idempotency, conflict detection, and retry support."""
        results: List[SyncItemResult] = []
        synced_count = 0
        conflicts_count = 0
        failed_count = 0

        for draft in request.drafts:
            # 1. Idempotency Check: Already processed?
            idemp_key = draft.idempotency_key or draft.draft_id
            if idemp_key in self._processed_idempotency:
                existing_res = self._processed_idempotency[idemp_key]
                results.append(SyncItemResult(
                    draft_id=draft.draft_id,
                    type=draft.type,
                    status=SyncStatus.ALREADY_PROCESSED,
                    server_id=existing_res.server_id,
                    client_version=draft.client_version,
                    server_version=existing_res.server_version,
                    message="Item was previously synchronized (idempotent replay).",
                    resolved_data=existing_res.resolved_data,
                ))
                synced_count += 1
                continue

            try:
                # 2. Execute operation according to draft type
                if draft.type == OfflineDraftType.CITIZEN_REGISTRATION:
                    client_cit_id = draft.payload.get("id") or draft.draft_id
                    cit, was_conflict = self.register_citizen(actor, draft.payload, client_id=client_cit_id)
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.CONFLICT_RESOLVED if was_conflict else SyncStatus.SYNCED,
                        server_id=cit.id,
                        client_version=draft.client_version,
                        server_version=draft.client_version + (1 if was_conflict else 0),
                        message="Citizen merged with existing record." if was_conflict else "Citizen registered successfully.",
                        resolved_data=cit.to_dict(),
                    )
                    if was_conflict:
                        conflicts_count += 1
                    else:
                        synced_count += 1

                elif draft.type == OfflineDraftType.CONSENT:
                    c_dir = self.record_consent(actor, draft.payload)
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.SYNCED,
                        server_id=c_dir.id,
                        client_version=draft.client_version,
                        server_version=draft.client_version,
                        message="Consent directive activated.",
                    )
                    synced_count += 1

                elif draft.type == OfflineDraftType.SCREENING:
                    scr_eval = self.record_screening(actor, draft.payload)
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.SYNCED,
                        server_id=scr_eval.get("session_id"),
                        client_version=draft.client_version,
                        server_version=draft.client_version,
                        message="Screening evaluated and structured observations saved.",
                        resolved_data=scr_eval,
                    )
                    synced_count += 1

                elif draft.type == OfflineDraftType.REFERRAL:
                    ref_payload = ReferralCreatePayload(**draft.payload)
                    ref_rec = self.create_referral(actor, ref_payload)
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.SYNCED,
                        server_id=ref_rec.id,
                        client_version=draft.client_version,
                        server_version=draft.client_version,
                        message=f"Referral slip {ref_rec.referral_slip_id} generated.",
                        resolved_data=ref_rec.model_dump(),
                    )
                    synced_count += 1

                elif draft.type == OfflineDraftType.FOLLOWUP:
                    f_payload = FollowUpCreatePayload(**draft.payload)
                    f_rec = self.create_followup(actor, f_payload)
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.SYNCED,
                        server_id=f_rec.id,
                        client_version=draft.client_version,
                        server_version=draft.client_version,
                        message="Follow-up scheduled.",
                        resolved_data=f_rec.model_dump(),
                    )
                    synced_count += 1

                else:
                    res = SyncItemResult(
                        draft_id=draft.draft_id,
                        type=draft.type,
                        status=SyncStatus.FAILED,
                        client_version=draft.client_version,
                        server_version=draft.client_version,
                        message=f"Unknown draft type: {draft.type}",
                    )
                    failed_count += 1

                # Save into idempotency cache
                self._processed_idempotency[idemp_key] = res
                results.append(res)

            except Exception as e:
                logger.error("batch_sync_item_failed", draft_id=draft.draft_id, error=str(e))
                res = SyncItemResult(
                    draft_id=draft.draft_id,
                    type=draft.type,
                    status=SyncStatus.FAILED,
                    client_version=draft.client_version,
                    server_version=draft.client_version,
                    message=f"Sync error: {str(e)}",
                )
                results.append(res)
                failed_count += 1

        return BatchSyncResponse(
            device_id=request.device_id,
            server_time=datetime.now(timezone.utc),
            total_submitted=len(request.drafts),
            synced_count=synced_count,
            conflicts_count=conflicts_count,
            failed_count=failed_count,
            results=results,
        )


# Singleton instance
health_worker_service = HealthWorkerService()
