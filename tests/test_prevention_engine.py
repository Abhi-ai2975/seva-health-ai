import pytest
from datetime import date, datetime, timezone
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from services.intervention_engine.models import (
    InterventionCategory,
    PriorityRank,
    PreventionPlanInput,
    ComprehensivePreventionPlan,
)
from services.intervention_engine.engine import PreventionInterventionEngine
from services.intervention_engine.rules import rule_registry, InterventionRule
from services.intervention_engine.templates import PREVENTION_TEMPLATES
from packages.types.enums import UserRole
from packages.auth.jwt import create_access_token

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_all_demo_data()


@pytest.fixture
def auth_headers():
    token = create_access_token("doctor-01", "karnataka_state_health", UserRole.CLINICIAN)
    return {"Authorization": f"Bearer {token}"}



# =========================================================================
# 1. CORE PREVENTION INTERVENTION ENGINE UNIT TESTS
# =========================================================================

def test_generate_comprehensive_plan_all_outputs_present():
    engine = PreventionInterventionEngine()
    plan_input = PreventionPlanInput(
        citizen_id="c-test-01",
        risk_profile={
            "overall_tier": "HIGH",
            "overall_score": 0.68,
            "domains": {
                "diabetes_risk": 0.72,
                "hypertension_risk": 0.60,
                "cardiovascular_risk": 0.45,
                "metabolic_syndrome_risk": 0.65,
                "ckd_risk": 0.20,
            },
        },
        risk_trajectory="WORSENING",
        lifestyle={
            "smoking": False,
            "alcohol": False,
            "sleep_hours": 6.0,
            "steps": 4500,
        },
        goals=["Lower fasting blood sugar", "Reduce waist by 2 cm"],
        preferences={
            "dietary_pattern": "VEGETARIAN",
            "exercise_type": "WALKING",
            "reminder_channel": "WHATSAPP",
            "preferred_reminder_time": "07:30 AM",
        },
        constraints=["KNEE_OSTEOARTHRITIS"],
        available_community_resources=[
            {"name": "Local Community Park", "type": "WALKING_TRACK"},
        ],
        clinician_recommendations=["Limit table salt to under 3g/day", "Prioritize whole millets"],
    )

    plan = engine.generate_plan(plan_input)

    # Output validations
    assert plan.citizen_id == "c-test-01"
    assert (plan.end_date - plan.start_date).days == 30
    assert len(plan.daily_tasks) == 30

    # 1. Goals
    assert len(plan.goals) >= 3
    assert any("millets" in g.title.lower() or "glycemic" in g.title.lower() or "sugar" in g.title.lower() for g in plan.goals)

    # 2. Actions
    assert len(plan.actions) >= 4
    for action in plan.actions:
        assert action.frequency != ""
        assert action.priority_level in [PriorityRank.SAFETY, PriorityRank.EVIDENCE, PriorityRank.EXPECTED_BENEFIT, PriorityRank.FEASIBILITY, PriorityRank.PREFERENCE]

    # 3. Reminders
    assert len(plan.reminders) >= 2
    assert all(r.channel == "WHATSAPP" for r in plan.reminders)
    assert all(r.scheduled_time == "07:30 AM" for r in plan.reminders)

    # 4. Educational Content
    assert len(plan.educational_content) >= 2
    for edu in plan.educational_content:
        assert edu.key_takeaway != ""
        assert edu.cultural_adaptation != ""

    # 5. Measurements
    assert len(plan.measurements) >= 2
    biometrics = [m.biometric for m in plan.measurements]
    assert "FASTING_GLUCOSE" in biometrics or "BLOOD_PRESSURE" in biometrics

    # 6. Follow-Up Schedule
    assert len(plan.follow_up_schedule) >= 2
    providers = [f.provider_role for f in plan.follow_up_schedule]
    assert "ASHA_WORKER" in providers or "CLINICIAN" in providers


def test_5_tier_prioritization_hierarchy_sorting():
    engine = PreventionInterventionEngine()
    plan_input = PreventionPlanInput(
        citizen_id="c-sort-test",
        risk_profile={"overall_tier": "HIGH", "domains": {"diabetes_risk": 0.70, "hypertension_risk": 0.65}},
        lifestyle={"smoking": True},  # Safety critical smoking rule triggered
        constraints=["KNEE_OSTEOARTHRITIS"],  # Triggers safety adaptation
    )

    plan = engine.generate_plan(plan_input)
    actions = plan.actions

    # Verify actions are strictly sorted by PriorityRank:
    # 1 (SAFETY) <= 2 (EVIDENCE) <= 3 (EXPECTED_BENEFIT) <= 4 (FEASIBILITY) <= 5 (PREFERENCE)
    for i in range(len(actions) - 1):
        assert actions[i].priority_level.value <= actions[i + 1].priority_level.value


def test_constraints_handling_and_safe_substitution():
    """Verify that a constraint like KNEE_OSTEOARTHRITIS adapts contraindicated
    high-impact actions into safe chair yoga / non-impact alternatives.
    """
    engine = PreventionInterventionEngine()
    plan_input = PreventionPlanInput(
        citizen_id="c-constraint-test",
        risk_profile={"overall_tier": "MODERATE", "domains": {"diabetes_risk": 0.55}},
        constraints=["KNEE_OSTEOARTHRITIS"],
    )

    plan = engine.generate_plan(plan_input)
    act_titles = [a.title for a in plan.actions]

    # Verify that safe alternative was applied
    assert any("Seated" in t or "Chair" in t or "Non-impact" in str(a.safety_notes or "") for t, a in zip(act_titles, plan.actions))
    # Verify safety note is attached
    adapted_action = next((a for a in plan.actions if a.safety_notes and "KNEE_OSTEOARTHRITIS" in a.safety_notes), None)
    assert adapted_action is not None


def test_strict_medication_governance():
    """Critical Safety Test:
    - Never prescribe medication autonomously
    - Never tell citizen to stop or alter medication
    - Disclaimer must be attached
    - Clinician workflow flagged when high risk
    """
    engine = PreventionInterventionEngine()
    plan_input = PreventionPlanInput(
        citizen_id="c-med-test",
        risk_profile={"overall_tier": "HIGH", "domains": {"diabetes_risk": 0.80}},
        risk_trajectory="WORSENING",
    )

    plan = engine.generate_plan(plan_input)

    # 1. Disclaimer must be present and explicit
    assert "CRITICAL MEDICATION SAFETY" in plan.clinician_medication_disclaimer
    assert "Never stop or alter prescribed medications without direct consultation" in plan.clinician_medication_disclaimer

    # 2. Clinician workflow must be required for high risk / worsening trajectory
    assert plan.clinician_workflow_required is True
    assert plan.clinician_review_status == "PENDING"

    # 3. Actions and educational content must NOT prescribe pharmacotherapy
    forbidden_terms = ["prescribe metformin", "start amlodipine", "take statin daily", "stop your blood pressure pill", "discontinue your insulin"]
    for action in plan.actions:
        for term in forbidden_terms:
            assert term not in action.description.lower()
            assert term not in action.title.lower()


def test_support_for_all_9_intervention_domains():
    """Verify rules and coverage across all 9 mandatory pillars:
    nutrition, physical activity, sleep, weight, smoking, alcohol, stress, screening, follow-up.
    """
    all_rules = rule_registry.list_rules()
    categories = {r.category.value for r in all_rules}

    expected_categories = {
        "nutrition",
        "physical_activity",
        "sleep",
        "weight_management",
        "smoking_cessation",
        "alcohol_reduction",
        "stress_management",
        "screening_adherence",
        "clinical_followup",
    }
    assert expected_categories.issubset(categories)


def test_prevention_templates_catalog():
    templates = list(PREVENTION_TEMPLATES.values())
    assert len(templates) >= 5
    template_ids = [t.template_id for t in templates]
    assert "prediabetes_reversal" in template_ids
    assert "hypertension_vascular" in template_ids
    assert "cardiovascular_protection" in template_ids
    assert "metabolic_weight_loss" in template_ids
    assert "renal_preservation" in template_ids


def test_configurable_rules_registry():
    # Test retrieving rules
    rule = rule_registry.get_rule("rule-nutri-millet")
    assert rule is not None
    assert rule.name == "Traditional Millet Substitution for Glycemic Control"

    # Test toggling rule status
    rule_registry.update_rule_status("rule-nutri-millet", False)
    assert rule_registry.get_rule("rule-nutri-millet").is_active is False

    # Restore rule status
    rule_registry.update_rule_status("rule-nutri-millet", True)
    assert rule_registry.get_rule("rule-nutri-millet").is_active is True


# =========================================================================
# 2. API GATEWAY INTEGRATION TESTS
# =========================================================================

def test_api_list_templates(auth_headers):
    response = client.get("/api/v1/intervention/templates", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 5
    assert any(t["template_id"] == "prediabetes_reversal" for t in data)


def test_api_list_rules(auth_headers):
    response = client.get("/api/v1/intervention/rules", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 10
    categories = [r["category"] for r in data]
    assert "nutrition" in categories
    assert "physical_activity" in categories
    assert "smoking_cessation" in categories


def test_api_generate_comprehensive_plan_ramesh_patel(auth_headers):
    response = client.post("/api/v1/intervention/generate-comprehensive/citizen-ramesh-patel-01", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["citizen_id"] == "citizen-ramesh-patel-01"
    assert len(data["goals"]) >= 3
    assert len(data["actions"]) >= 4
    assert len(data["daily_tasks"]) == 30
    assert len(data["reminders"]) >= 2
    assert len(data["educational_content"]) >= 2
    assert len(data["measurements"]) >= 2
    assert len(data["follow_up_schedule"]) >= 2
    assert "CRITICAL MEDICATION SAFETY" in data["clinician_medication_disclaimer"]
    assert data["clinician_workflow_required"] is True


def test_api_get_comprehensive_plan(auth_headers):
    response = client.get("/api/v1/intervention/comprehensive/citizen-ramesh-patel-01", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["citizen_id"] == "citizen-ramesh-patel-01"
    assert len(data["daily_tasks"]) == 30


def test_api_backward_compatible_plan_endpoints(auth_headers):
    response = client.get("/api/v1/intervention/plan/citizen-ramesh-patel-01", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["daily_tasks"]) == 30
    assert "adherence_percentage" in data

