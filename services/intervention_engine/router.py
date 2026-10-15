from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone

import uuid
from packages.clinical_models.care_plan import CarePlan, PlanModificationRecord, CitizenCheckIn
from packages.auth.jwt import get_current_user_token, get_current_user_token_optional, TokenPayload
from packages.types.enums import AuditAction, UserRole
from packages.observability.audit import audit_logger
from services.intervention_engine.generator import generate_30_day_care_plan
from services.intervention_engine.models import (
    ComprehensivePreventionPlan,
    PreventionPlanInput,
    PlanModificationRequest,
    CitizenCheckInCreate,
)
from services.intervention_engine.engine import prevention_intervention_engine
from services.intervention_engine.rules import rule_registry, InterventionRule
from services.intervention_engine.templates import PREVENTION_TEMPLATES, PreventionTemplate
from services.risk_engine.evaluator import evaluate_ncd_domains
from services.store import store

router = APIRouter(prefix="/intervention", tags=["Preventive Interventions"])


@router.get("/templates", response_model=List[PreventionTemplate])
async def list_prevention_templates(
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Lists available evidence-based 30-day prevention journey templates."""
    return list(PREVENTION_TEMPLATES.values())


@router.get("/rules", response_model=List[InterventionRule])
async def list_configurable_rules(
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Lists active configurable intervention rules and their prioritization rankings."""
    return rule_registry.list_rules()


@router.post("/generate-comprehensive/{citizen_id}", response_model=ComprehensivePreventionPlan)
async def generate_comprehensive_prevention_plan(
    citizen_id: str,
    custom_input: Optional[PreventionPlanInput] = None,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Generates a complete 30-day prevention care plan including:
    - Goals (SMART preventive targets)
    - Actions (Prioritized: Safety > Evidence > Benefit > Feasibility > Preference)
    - Daily Tasks (30-day interactive checklist)
    - Reminders (Scheduled notifications across channels)
    - Educational Content (Culturally adapted evidence modules)
    - Measurements (Biometric tracking schedule)
    - Follow-Up Schedule (ASHA, Nurse, Clinician checkpoints)
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    risk = store.get_latest_risk(citizen_id)
    trajectory_snapshots = store.get_trajectory_snapshots(citizen_id)
    latest_traj = "STABLE"
    if risk and hasattr(risk.trajectory, "value"):
        latest_traj = risk.trajectory.value

    # Prepare input payload
    if custom_input:
        plan_input = custom_input
        plan_input.citizen_id = citizen_id
        plan_input.tenant_id = citizen.tenant_id
        if not plan_input.risk_profile and risk:
            plan_input.risk_profile = {
                "overall_tier": risk.overall_tier.value,
                "overall_score": risk.overall_score,
                "domains": {
                    "diabetes_risk": risk.domains.diabetes_risk,
                    "hypertension_risk": risk.domains.hypertension_risk,
                    "cardiovascular_risk": risk.domains.cardiovascular_risk,
                    "metabolic_syndrome_risk": risk.domains.metabolic_syndrome_risk,
                    "ckd_risk": risk.domains.ckd_risk,
                }
            }
        if not plan_input.risk_trajectory:
            plan_input.risk_trajectory = latest_traj
    else:
        # Build from recorded citizen profile & risk
        domains_dict = {}
        if risk:
            domains_dict = {
                "diabetes_risk": risk.domains.diabetes_risk,
                "hypertension_risk": risk.domains.hypertension_risk,
                "cardiovascular_risk": risk.domains.cardiovascular_risk,
                "metabolic_syndrome_risk": risk.domains.metabolic_syndrome_risk,
                "ckd_risk": risk.domains.ckd_risk,
            }
        plan_input = PreventionPlanInput(
            citizen_id=citizen_id,
            tenant_id=citizen.tenant_id,
            risk_profile={
                "overall_tier": risk.overall_tier.value if risk else "MODERATE",
                "overall_score": risk.overall_score if risk else 0.40,
                "domains": domains_dict,
            },
            risk_trajectory=latest_traj,
            lifestyle={},
            goals=["Improve glycemic stability", "Walk 8,000 steps daily"],
            preferences={"dietary_pattern": "VEGETARIAN", "exercise_type": "WALKING"},
            constraints=[],
            available_community_resources=[
                {"name": "Ward 12 Community Park", "type": "WALKING_TRACK"},
                {"name": "Devanahalli PHC Yoga Center", "type": "YOGA_WELLNESS"},
            ],
            clinician_recommendations=[],
        )

    # Synthesize comprehensive plan
    plan = prevention_intervention_engine.generate_plan(plan_input)
    store.set_comprehensive_plan(plan)

    # Also sync backward-compatible CarePlan into store
    backward_care_plan = generate_30_day_care_plan(
        citizen_id=citizen_id,
        tenant_id=citizen.tenant_id,
        risk=risk if risk else store.get_latest_risk(citizen_id),
    )
    store.set_care_plan(backward_care_plan)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CARE_PLAN_GENERATED,
        resource_type="ComprehensivePreventionPlan",
        resource_id=plan.id,
    )

    return plan


@router.get("/comprehensive/{citizen_id}", response_model=ComprehensivePreventionPlan)
async def get_comprehensive_prevention_plan(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Retrieves the active 30-day comprehensive prevention plan for a citizen."""
    plan = store.get_comprehensive_plan(citizen_id)
    if not plan:
        return await generate_comprehensive_prevention_plan(citizen_id, None, current_user)
    return plan


# =========================================================================
# BACKWARD COMPATIBILITY ENDPOINTS (Preserved for existing frontend/tests)
# =========================================================================

@router.post("/generate/{citizen_id}", response_model=CarePlan)
async def generate_citizen_care_plan(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    risk = store.get_latest_risk(citizen_id)
    if not risk:
        # Evaluate risk if not yet run
        obs = store.get_citizen_observations(citizen_id)
        domains, score, tier, traj, drivers, prot = evaluate_ncd_domains(obs, gender=citizen.gender.value)
        from packages.clinical_models.risk import RiskAssessment
        import uuid
        risk = RiskAssessment(
            id=str(uuid.uuid4()),
            tenant_id=citizen.tenant_id,
            citizen_id=citizen_id,
            overall_score=score,
            overall_tier=tier,
            domains=domains,
            trajectory=traj,
            confidence_score=0.85,
            top_drivers=drivers,
            protective_factors=prot,
            clinical_summary="Auto-evaluated for care plan generation.",
        )
        store.add_risk_assessment(risk)

    plan = generate_30_day_care_plan(
        citizen_id=citizen_id,
        tenant_id=citizen.tenant_id,
        risk=risk,
    )
    store.set_care_plan(plan)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CARE_PLAN_GENERATED,
        resource_type="CarePlan",
        resource_id=plan.id,
    )
    return plan


@router.get("/plan/{citizen_id}", response_model=CarePlan)
async def get_citizen_care_plan(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    plan = store.get_care_plan(citizen_id)
    if not plan:
        return await generate_citizen_care_plan(citizen_id, current_user)
    return plan


@router.post("/tasks/{citizen_id}/{task_id}/toggle", response_model=CarePlan)
async def toggle_task_completion(
    citizen_id: str,
    task_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    plan = store.get_care_plan(citizen_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Care plan not found")

    target_task = next((t for t in plan.daily_tasks if t.id == task_id), None)
    if not target_task:
        raise HTTPException(status_code=404, detail="Task not found")

    target_task.completed = not target_task.completed
    target_task.completed_at = datetime.now(timezone.utc) if target_task.completed else None

    # Recalculate adherence percentage
    completed_count = sum(1 for t in plan.daily_tasks if t.completed)
    plan.adherence_percentage = round((completed_count / len(plan.daily_tasks)) * 100, 1)

    store.set_care_plan(plan)

    audit_logger.record(
        tenant_id=plan.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.TASK_COMPLETED if target_task.completed else AuditAction.CARE_PLAN_GENERATED,
        resource_type="DailyTask",
        resource_id=task_id,
    )

    return plan


@router.put("/plan/{citizen_id}", response_model=CarePlan)
@router.patch("/plan/{citizen_id}", response_model=CarePlan)
async def modify_citizen_care_plan(
    citizen_id: str,
    request: PlanModificationRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Enables authorized clinicians to review and modify active care plans (TC-PLN-002).
    Enforces RBAC (Clinicians, Doctors, Super Admins only).
    Persists changes with actor identity, timestamp, and version history.
    """
    # Verify RBAC - Clinician authorization
    authorized_roles = [UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN]
    role_val = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    allowed_str_roles = [r.value if hasattr(r, "value") else str(r) for r in authorized_roles]
    if role_val not in allowed_str_roles:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only authorized clinicians may modify clinical prevention plans."
        )

    plan = store.get_care_plan(citizen_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Care plan not found")

    old_version = getattr(plan, "version", 1)
    new_version = old_version + 1
    fields_modified = []

    if request.goals is not None:
        plan.goals = request.goals
        fields_modified.append("goals")
    if request.nutrition_guidance is not None:
        plan.nutrition_guidance = request.nutrition_guidance
        fields_modified.append("nutrition_guidance")
    if request.activity_guidance is not None:
        plan.activity_guidance = request.activity_guidance
        fields_modified.append("activity_guidance")
    if request.sleep_guidance is not None:
        plan.sleep_guidance = request.sleep_guidance
        fields_modified.append("sleep_guidance")
    if request.stress_guidance is not None:
        plan.stress_guidance = request.stress_guidance
        fields_modified.append("stress_guidance")
    if request.limitations is not None:
        plan.limitations = request.limitations
        fields_modified.append("limitations")
    if request.follow_up_timing is not None:
        plan.follow_up_timing = request.follow_up_timing
        fields_modified.append("follow_up_timing")
    if request.frequency is not None:
        plan.frequency = request.frequency
        fields_modified.append("frequency")
    if request.actions is not None:
        plan.actions = request.actions
        fields_modified.append("actions")
    if request.daily_tasks is not None:
        plan.daily_tasks = request.daily_tasks
        fields_modified.append("daily_tasks")

    now = datetime.now(timezone.utc)
    plan.version = new_version
    plan.clinician_reviewed = True
    plan.clinician_id = current_user.sub
    plan.updated_at = now

    record = PlanModificationRecord(
        plan_id=plan.id,
        actor_id=current_user.sub,
        actor_role=role_val,
        timestamp=now,
        version_before=old_version,
        version_after=new_version,
        fields_modified=fields_modified,
        reason=request.reason,
        notes=request.notes,
    )
    plan.history.append(record)
    store.set_care_plan(plan)

    # Sync comprehensive plan if present
    comp_plan = store.get_comprehensive_plan(citizen_id)
    if comp_plan:
        comp_plan.version = new_version
        comp_plan.clinician_review_status = "APPROVED"
        comp_plan.clinician_id = current_user.sub
        comp_plan.updated_at = now
        if hasattr(comp_plan, "history"):
            comp_plan.history.append(record)
        store.set_comprehensive_plan(comp_plan)

    audit_logger.record(
        tenant_id=plan.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.CARE_PLAN_REVIEWED,
        resource_type="CarePlan",
        resource_id=plan.id,
    )

    return plan


@router.get("/plan/{citizen_id}/history", response_model=List[PlanModificationRecord])
async def get_plan_history(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Retrieves full version and modification history of a citizen's care plan (TC-PLN-002)."""
    plan = store.get_care_plan(citizen_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Care plan not found")
    return plan.history


@router.post("/check-in/{citizen_id}", response_model=CitizenCheckIn)
async def submit_citizen_checkin(
    citizen_id: str,
    payload: Optional[CitizenCheckInCreate] = None,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Submits a citizen daily health check-in (TC-PLN-003).
    Associates the check-in with the active care plan and persists it in the database.
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    plan = store.get_care_plan(citizen_id)
    if not plan:
        plan = await generate_citizen_care_plan(citizen_id, current_user)

    if payload is None:
        payload = CitizenCheckInCreate()

    check_in = CitizenCheckIn(
        citizen_id=citizen_id,
        plan_id=plan.id,
        timestamp=datetime.now(timezone.utc),
        feeling=payload.feeling,
        medication_adherence=payload.medication_adherence,
        symptoms=payload.symptoms,
        notes=payload.notes,
        tasks_completed_count=payload.tasks_completed_count or 0,
    )

    store.add_checkin(citizen_id, check_in.model_dump() if hasattr(check_in, "model_dump") else check_in.dict())

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.OBSERVATION_RECORDED,
        resource_type="CitizenCheckIn",
        resource_id=check_in.id,
    )

    return check_in


@router.get("/check-in/{citizen_id}", response_model=List[CitizenCheckIn])
async def get_citizen_checkins(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Retrieves all historical check-ins for a citizen (TC-PLN-003)."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    raw_checkins = store.get_checkins(citizen_id)
    results: List[CitizenCheckIn] = []
    for item in raw_checkins:
        if isinstance(item, CitizenCheckIn):
            results.append(item)
        elif isinstance(item, dict):
            # Parse timestamp if string
            ts = item.get("timestamp") or item.get("recorded_at")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except Exception:
                    ts = datetime.now(timezone.utc)
            elif not isinstance(ts, datetime):
                ts = datetime.now(timezone.utc)

            plan_id = item.get("plan_id")
            if not plan_id:
                active_p = store.get_care_plan(citizen_id)
                plan_id = active_p.id if active_p else "active-plan"

            results.append(CitizenCheckIn(
                id=item.get("id") or str(uuid.uuid4()),
                citizen_id=item.get("citizen_id", citizen_id),
                plan_id=plan_id,
                timestamp=ts,
                feeling=item.get("feeling") or item.get("subjective_wellbeing") or "Normal",
                medication_adherence=item.get("medication_adherence", True),
                symptoms=item.get("symptoms", []),
                notes=item.get("notes"),
                tasks_completed_count=item.get("tasks_completed_count", 0),
            ))

    return results


@router.get("/database/all")
async def get_all_intervention_database_records(
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Returns all stored care plans, plan modifications, and check-ins for Database Explorer."""
    plans = []
    for cid, p in store.care_plans.items():
        plans.append(p.dict() if hasattr(p, "dict") else str(p))
    
    all_checkins = []
    for cid, c_list in store.checkins.items():
        for c in c_list:
            all_checkins.append(c.dict() if hasattr(c, "dict") else c if isinstance(c, dict) else str(c))
            
    all_history = []
    for cid, p in store.care_plans.items():
        if hasattr(p, "history"):
            for h in p.history:
                all_history.append(h.dict() if hasattr(h, "dict") else h)

    return {
        "plans": plans,
        "checkins": all_checkins,
        "history": all_history,
        "total_plans": len(plans),
        "total_checkins": len(all_checkins),
        "total_modifications": len(all_history),
    }

