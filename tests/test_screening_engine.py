import pytest
from services.screening.questionnaire_engine import (
    QuestionnaireEngine,
    calculate_derived_bmi,
    calculate_derived_egfr,
    derive_idrs_from_answers,
    derive_cbac_from_answers,
)
from services.screening.questionnaire_models import (
    ConfigurableScreeningSubmission,
    ScreeningSectionType,
    QuestionType,
)
from packages.types.enums import Gender
from services.store import store, CitizenRecord


@pytest.fixture(autouse=True)
def ensure_test_citizen():
    cit_id = "cit-screen-test-01"
    if not store.get_citizen(cit_id):
        cit = CitizenRecord(
            id=cit_id,
            tenant_id="tenant-karnataka-moh",
            user_id="user-cit-01",
            abha_id="91-0000-1111-2222",
            first_name="Ramesh",
            last_name="Verma",
            birth_date="1975-04-12",
            gender=Gender.MALE,
            phone="+919876543210",
            state="Karnataka",
            district="Mandya",
            sub_district="Maddur",
            village_or_ward="Ward-12",
        )
        store.add_citizen(cit)
    return cit_id


def test_questionnaire_config_profiles():
    # 1. MVP Comprehensive
    comp = QuestionnaireEngine.get_profile("mvp_comprehensive")
    assert comp.id == "mvp_comprehensive"
    assert len(comp.sections) == 6
    
    sections = {s.id for s in comp.sections}
    assert sections == {
        ScreeningSectionType.DEMOGRAPHICS,
        ScreeningSectionType.ANTHROPOMETRY,
        ScreeningSectionType.VITALS,
        ScreeningSectionType.LABS,
        ScreeningSectionType.LIFESTYLE,
        ScreeningSectionType.HISTORY,
    }

    # Verify key questions have LOINC codes and units
    all_questions = {q.id: q for s in comp.sections for q in s.questions}
    assert "height" in all_questions
    assert all_questions["height"].unit == "cm"
    assert all_questions["height"].loinc_code == "8302-2"

    assert "systolic_bp" in all_questions
    assert all_questions["systolic_bp"].unit == "mmHg"
    assert all_questions["systolic_bp"].loinc_code == "8480-6"

    assert "hba1c" in all_questions
    assert all_questions["hba1c"].unit == "%"
    assert all_questions["hba1c"].loinc_code == "4548-4"

    # 2. ASHA Rapid
    asha = QuestionnaireEngine.get_profile("asha_field_rapid")
    assert asha.id == "asha_field_rapid"
    assert asha.target_role == "HEALTH_WORKER"

    # 3. Citizen Self Check
    cit = QuestionnaireEngine.get_profile("citizen_self_check")
    assert cit.id == "citizen_self_check"
    assert cit.target_role == "CITIZEN"


def test_automatic_derivations():
    # BMI Derivation
    bmi = calculate_derived_bmi(weight_kg=78.5, height_cm=168.0)
    assert bmi is not None
    assert 27.5 <= bmi <= 28.0

    # eGFR Derivation (CKD-EPI)
    egfr_male = calculate_derived_egfr(serum_creatinine=1.0, age=50, sex="MALE")
    assert egfr_male is not None
    assert 80.0 <= egfr_male <= 105.0

    egfr_female = calculate_derived_egfr(serum_creatinine=0.8, age=50, sex="FEMALE")
    assert egfr_female is not None
    assert 85.0 <= egfr_female <= 110.0

    # IDRS Score Derivation
    answers = {
        "age": 52,                   # >= 50 -> +30
        "sex": "MALE",
        "waist_circumference": 98,   # >= 90 -> +10
        "physical_activity": "None", # None -> +30
        "family_history": ["DIABETES_ONE_PARENT"], # +10
    }
    idrs = derive_idrs_from_answers(answers)
    assert idrs == 80  # 30 + 10 + 30 + 10 = 80 (High Risk >= 60)

    # CBAC Score Derivation
    answers_cbac = {
        "age": 45,                   # > 30 -> +2
        "smoking": "CURRENT_SMOKER", # +2
        "alcohol": "REGULAR",        # +1
        "sex": "MALE",
        "waist_circumference": 96,   # +2
        "physical_activity": "Sedentary", # +2
        "family_history": ["HYPERTENSION"], # +2
    }
    cbac = derive_cbac_from_answers(answers_cbac)
    assert cbac >= 4  # Triggers primary health referral


def test_configurable_submission_and_structured_observations(ensure_test_citizen):
    cit_id = ensure_test_citizen
    submission = ConfigurableScreeningSubmission(
        citizen_id=cit_id,
        workflow_id="mvp_comprehensive",
        answers={
            "age": 50,
            "sex": "MALE",
            "height": 170.0,
            "weight": 76.0,
            "waist_circumference": 94.0,
            "systolic_bp": 138.0,
            "diastolic_bp": 88.0,
            "heart_rate": 76.0,
            "fasting_glucose": 112.0,
            "hba1c": 6.1,
            "total_cholesterol": 220.0,
            "ldl_cholesterol": 140.0,
            "hdl_cholesterol": 42.0,
            "triglycerides": 180.0,
            "serum_creatinine": 1.0,
            "physical_activity": "Sedentary",
            "diet_quality": "High refined carbs / sweets / fried foods",
            "sleep_hours": 5.5,
            "smoking": "CURRENT_SMOKER",
            "alcohol": "NONE",
            "stress_level": 7.0,
            "family_history": ["DIABETES_ONE_PARENT"],
            "known_conditions": ["NONE"],
            "medication_history": ["NONE"],
        },
        notes="ASHA field validation camp in Mandya.",
    )

    result = QuestionnaireEngine.evaluate_submission(
        submission=submission,
        actor_id="worker-asha-01",
        actor_role="HEALTH_WORKER",
    )

    assert result.citizen_id == cit_id
    assert result.structured_observations_count >= 12

    # Check structured observations have LOINC codes, non-empty units, and valid values
    loinc_codes = {obs.loinc_code for obs in result.structured_observations}
    assert "8480-6" in loinc_codes  # Systolic BP
    assert "8462-4" in loinc_codes  # Diastolic BP
    assert "1558-6" in loinc_codes  # Fasting Glucose
    assert "4548-4" in loinc_codes  # HbA1c
    assert "39156-5" in loinc_codes # Derived BMI

    for obs in result.structured_observations:
        assert obs.citizen_id == cit_id
        assert obs.unit != ""
        assert obs.source == "CONFIGURABLE_SCREENING"


def test_explainable_summary_generation(ensure_test_citizen):
    cit_id = ensure_test_citizen
    submission = ConfigurableScreeningSubmission(
        citizen_id=cit_id,
        workflow_id="mvp_comprehensive",
        answers={
            "age": 48,
            "sex": "MALE",
            "height": 165.0,
            "weight": 82.0,
            "waist_circumference": 98.0,
            "systolic_bp": 145.0,
            "diastolic_bp": 92.0,
            "fasting_glucose": 120.0,
            "hba1c": 6.3,
            "physical_activity": "None",
            "diet_quality": "High refined carbs / sweets / fried foods",
            "sleep_hours": 5.0,
            "smoking": "CURRENT_SMOKER",
            "alcohol": "NONE",
            "stress_level": 8.0,
            "family_history": ["DIABETES_BOTH_PARENTS"],
        },
    )

    result = QuestionnaireEngine.evaluate_submission(submission=submission)
    summary = result.explainable_summary

    assert summary.citizen_id == cit_id
    assert summary.overall_tier in ["HIGH", "CRITICAL"]
    assert summary.trajectory == "DETERIORATING"
    assert summary.composite_risk_score > 0.50

    # Top drivers must have explainability features
    assert len(summary.top_drivers) >= 2
    top_driver = summary.top_drivers[0]
    assert top_driver.feature_name != ""
    assert top_driver.observed_value != ""
    assert top_driver.target_value != ""
    assert top_driver.evidence_citation != ""

    # Human-in-the-loop action directives
    assert len(summary.immediate_lifestyle_actions) >= 2
    assert len(summary.recommended_diagnostic_followups) >= 1
    assert summary.requires_clinical_escalation is True
    assert summary.escalation_urgency in ["PRIORITY", "EMERGENT"]

    # Non-diagnostic clinical safety disclaimer
    assert "DECISION SUPPORT NOTICE" in summary.clinical_safety_notice
    assert "NOT constitute a confirmed medical diagnosis" in summary.clinical_safety_notice


@pytest.mark.asyncio
async def test_demo_screening_scenarios(ensure_test_citizen):
    from services.screening.router import evaluate_demo_screening
    from packages.auth.jwt import TokenPayload
    from packages.types.enums import UserRole

    worker_token = TokenPayload(
        sub="worker-asha-demo",
        role=UserRole.HEALTH_WORKER,
        tenant_id="tenant-karnataka-moh",
    )

    # 1. ASHA Field Camp Demo
    res_asha = await evaluate_demo_screening(scenario="rajesh_kumar_asha", current_user=worker_token)
    assert res_asha.structured_observations_count >= 10
    assert res_asha.explainable_summary.overall_tier in ["HIGH", "CRITICAL"]
    assert res_asha.explainable_summary.idrs_score is not None

    # 2. Citizen Self-Check Demo
    res_citizen = await evaluate_demo_screening(scenario="sunita_sharma_citizen", current_user=worker_token)
    assert res_citizen.structured_observations_count >= 8
    assert res_citizen.explainable_summary.calculated_bmi is not None

    # 3. Clinic Intake Demo
    res_clinic = await evaluate_demo_screening(scenario="vikram_singh_clinic", current_user=worker_token)
    assert res_clinic.structured_observations_count >= 12
    assert res_clinic.explainable_summary.calculated_egfr is not None

