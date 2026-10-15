from datetime import date, timedelta
from typing import List, Optional
import uuid

from packages.types.enums import InterventionPillar
from packages.clinical_models.care_plan import CarePlan, DailyTask
from packages.clinical_models.risk import RiskAssessment
from services.intervention_engine.models import PreventionPlanInput
from services.intervention_engine.engine import prevention_intervention_engine


def generate_30_day_care_plan(
    citizen_id: str,
    tenant_id: str,
    risk: RiskAssessment,
    start_date: Optional[date] = None,
) -> CarePlan:
    """Generates an evidence-based 30-day preventive lifestyle medicine care plan.
    Delegates to PreventionInterventionEngine while maintaining strict backward-compatibility
    with the CarePlan contract.
    """
    if not start_date:
        start_date = date.today()
    end_date = start_date + timedelta(days=30)

    # Convert RiskAssessment into PreventionPlanInput
    domains_dict = {
        "diabetes_risk": risk.domains.diabetes_risk,
        "hypertension_risk": risk.domains.hypertension_risk,
        "cardiovascular_risk": risk.domains.cardiovascular_risk,
        "metabolic_syndrome_risk": risk.domains.metabolic_syndrome_risk,
        "ckd_risk": risk.domains.ckd_risk,
    }
    plan_input = PreventionPlanInput(
        citizen_id=citizen_id,
        tenant_id=tenant_id,
        risk_profile={
            "overall_tier": risk.overall_tier.value if hasattr(risk.overall_tier, "value") else str(risk.overall_tier),
            "overall_score": risk.overall_score,
            "domains": domains_dict,
        },
        risk_trajectory=risk.trajectory.value if hasattr(risk.trajectory, "value") else str(risk.trajectory),
        lifestyle={},
        goals=[],
        preferences={"dietary_pattern": "VEGETARIAN", "exercise_type": "WALKING"},
        constraints=[],
    )

    comp_plan = prevention_intervention_engine.generate_plan(plan_input, start_date=start_date)

    # Derive pillar guidance summaries from actions
    nutri_actions = [a.description for a in comp_plan.actions if a.category.value == "nutrition"]
    nutri_guidance = nutri_actions[0] if nutri_actions else "Switch 50% of refined grains to traditional whole millets (ragi, jowar, foxtail); limit table salt."

    act_actions = [a.description for a in comp_plan.actions if a.category.value == "physical_activity"]
    act_guidance = act_actions[0] if act_actions else "Target 150 minutes of moderate aerobic movement weekly, supplemented by daily short post-prandial walks."

    sleep_actions = [a.description for a in comp_plan.actions if a.category.value == "sleep"]
    sleep_guidance = sleep_actions[0] if sleep_actions else "Aim for 7 to 8 hours of uninterrupted sleep with screens off 45 minutes prior."

    stress_actions = [a.description for a in comp_plan.actions if a.category.value == "stress_management"]
    stress_guidance = stress_actions[0] if stress_actions else "Integrate short daily diaphragmatic pranayama breaks (10 minutes twice daily)."

    return CarePlan(
        id=comp_plan.id,
        tenant_id=tenant_id,
        citizen_id=citizen_id,
        risk_assessment_id=risk.id,
        title=comp_plan.title,
        focus_domain=comp_plan.focus_domain,
        start_date=start_date,
        end_date=end_date,
        adherence_percentage=0.0,
        nutrition_guidance=nutri_guidance,
        activity_guidance=act_guidance,
        sleep_guidance=sleep_guidance,
        stress_guidance=stress_guidance,
        daily_tasks=comp_plan.daily_tasks,
        goals=[g.title for g in comp_plan.goals],
        actions=[
            {
                "id": a.id,
                "title": a.title,
                "description": a.description,
                "frequency": a.frequency,
                "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                "priority_level": a.priority_level.value if hasattr(a.priority_level, "value") else a.priority_level,
                "evidence_citation": a.evidence_citation,
            }
            for a in comp_plan.actions
        ],
        frequency=comp_plan.frequency,
        follow_up_timing=comp_plan.follow_up_timing,
        limitations=list(comp_plan.limitations),
        version=1,
        history=[],
        clinician_reviewed=(not comp_plan.clinician_workflow_required),
    )

