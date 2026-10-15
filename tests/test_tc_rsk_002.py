"""TC-RSK-002: Missing essential inputs
Priority: P0
Requirement: SYS-RSK-001 through SYS-RSK-006

Test Steps:
1. Remove an essential input required by the selected model.
2. Request an assessment.
3. Verify that the engine returns an insufficient-data status and identifies the missing requirements.
4. Verify that the engine does NOT invent a score (score is None / null).
"""

import pytest
from datetime import datetime, timezone
import uuid
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, ConsentDirective
from packages.types.enums import UserRole, Gender, RiskTier
from packages.auth.jwt import create_access_token
from services.risk_engine.base import RiskCategory, DomainRiskResult
from services.risk_engine.models.hypertension import HypertensionRiskModel
from services.risk_engine.models.diabetes import DiabetesRiskModel
from services.risk_engine.models.cardiovascular import CardiovascularRiskModel
from services.risk_engine.models.obesity import ObesityMetabolicRiskModel
from services.risk_engine.models.ckd import CKDRiskModel
from services.risk_engine.engine import ncd_risk_engine

client = TestClient(app)


@pytest.fixture
def auth_tokens():
    """Generates valid JWT tokens for all required user personas."""
    return {
        "clinician": create_access_token(
            subject="doc-rsk-002",
            tenant_id="tenant-delhi-01",
            role=UserRole.CLINICIAN,
            scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
        ),
        "worker": create_access_token(
            subject="worker-rsk-002",
            tenant_id="tenant-delhi-01",
            role=UserRole.HEALTH_WORKER,
            scopes=["screening:create", "screening:read", "risk:read", "risk:create"],
        ),
    }


# =========================================================================
# 1. INDIVIDUAL MODEL TESTS: REMOVING ESSENTIAL INPUTS
# =========================================================================

def test_tc_rsk_002_hypertension_model_missing_systolic():
    """TC-RSK-002: Selected model = Hypertension.
    Essential inputs: SYSTOLIC_BP, DIASTOLIC_BP.
    Step: Remove SYSTOLIC_BP.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies SYSTOLIC_BP.
    """
    model = HypertensionRiskModel()
    assert "SYSTOLIC_BP" in model.required_inputs()
    assert "DIASTOLIC_BP" in model.required_inputs()

    # Inputs missing essential SYSTOLIC_BP
    inputs_without_sbp = {"DIASTOLIC_BP": 95.0, "HEART_RATE": 78.0}
    res = model.calculate(inputs_without_sbp)

    # 1. Returns insufficient-data status
    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    # 2. Does NOT invent a score
    assert res.score is None
    # 3. Identifies the missing requirements
    assert "SYSTOLIC_BP" in res.missing_requirements
    assert any("SYSTOLIC_BP" in limit for limit in res.limitations)
    assert res.confidence == 0.0


def test_tc_rsk_002_hypertension_model_missing_diastolic():
    """TC-RSK-002: Selected model = Hypertension.
    Step: Remove DIASTOLIC_BP.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies DIASTOLIC_BP.
    """
    model = HypertensionRiskModel()
    inputs_without_dbp = {"SYSTOLIC_BP": 150.0, "HEART_RATE": 82.0}
    res = model.calculate(inputs_without_dbp)

    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.score is None
    assert "DIASTOLIC_BP" in res.missing_requirements
    assert any("DIASTOLIC_BP" in limit for limit in res.limitations)


def test_tc_rsk_002_diabetes_model_missing_glycemic_markers():
    """TC-RSK-002: Selected model = Diabetes & Glycemic Risk.
    Essential inputs: AGE and at least one Glycemic Biomarker (HBA1C, FASTING_GLUCOSE, or IDRS_SCORE).
    Step: Remove all glycemic biomarkers.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies missing glycemic markers.
    """
    model = DiabetesRiskModel()
    # Provide only demographic data without glycemic markers
    inputs_without_glycemia = {"AGE": 52, "SEX": "MALE", "WAIST_CIRCUMFERENCE": 92.0}
    res = model.calculate(inputs_without_glycemia)

    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.score is None
    assert len(res.missing_requirements) >= 1
    assert any("Glycemic" in req or "HBA1C" in req for req in res.missing_requirements)
    assert res.confidence == 0.0


def test_tc_rsk_002_diabetes_model_missing_age():
    """TC-RSK-002: Selected model = Diabetes & Glycemic Risk.
    Step: Remove mandatory AGE.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies AGE.
    """
    model = DiabetesRiskModel()
    inputs_without_age = {"HBA1C": 7.6, "FASTING_GLUCOSE": 140.0}
    res = model.calculate(inputs_without_age)

    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.score is None
    assert "AGE" in res.missing_requirements


def test_tc_rsk_002_cardiovascular_model_missing_essential_inputs():
    """TC-RSK-002: Selected model = Cardiovascular (WHO-SEAR ASCVD).
    Essential inputs: AGE, SYSTOLIC_BP.
    Step: Remove SYSTOLIC_BP.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies SYSTOLIC_BP.
    """
    model = CardiovascularRiskModel()
    assert "AGE" in model.required_inputs()
    assert "SYSTOLIC_BP" in model.required_inputs()

    # Case A: Missing SYSTOLIC_BP
    inputs_no_sbp = {"AGE": 58, "TOTAL_CHOLESTEROL": 220.0, "SMOKING": True}
    res_no_sbp = model.calculate(inputs_no_sbp)
    assert res_no_sbp.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_no_sbp.score is None
    assert "SYSTOLIC_BP" in res_no_sbp.missing_requirements

    # Case B: Missing AGE
    inputs_no_age = {"SYSTOLIC_BP": 150.0, "TOTAL_CHOLESTEROL": 220.0}
    res_no_age = model.calculate(inputs_no_age)
    assert res_no_age.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_no_age.score is None
    assert "AGE" in res_no_age.missing_requirements


def test_tc_rsk_002_obesity_model_missing_essential_inputs():
    """TC-RSK-002: Selected model = Obesity & Metabolic Syndrome.
    Essential inputs: WAIST_CIRCUMFERENCE, and BMI (or HEIGHT + WEIGHT).
    Step: Remove WAIST_CIRCUMFERENCE.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies WAIST_CIRCUMFERENCE.
    """
    model = ObesityMetabolicRiskModel()
    assert "WAIST_CIRCUMFERENCE" in model.required_inputs()

    # Case A: Missing WAIST_CIRCUMFERENCE
    inputs_no_waist = {"BMI": 28.5}
    res_no_waist = model.calculate(inputs_no_waist)
    assert res_no_waist.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_no_waist.score is None
    assert "WAIST_CIRCUMFERENCE" in res_no_waist.missing_requirements

    # Case B: Missing BMI and Height/Weight
    inputs_no_bmi = {"WAIST_CIRCUMFERENCE": 96.0}
    res_no_bmi = model.calculate(inputs_no_bmi)
    assert res_no_bmi.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_no_bmi.score is None
    assert any("BMI" in req for req in res_no_bmi.missing_requirements)


def test_tc_rsk_002_ckd_model_missing_all_filtration_and_comorbidities():
    """TC-RSK-002: Selected model = CKD.
    Step: Remove all renal filtration markers and cardiovascular comorbidities.
    Expected: Returns INSUFFICIENT_DATA, score is None, identifies missing renal markers.
    """
    model = CKDRiskModel()
    inputs_empty = {"AGE": 45, "SEX": "FEMALE"}
    res = model.calculate(inputs_empty)

    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.score is None
    assert len(res.missing_requirements) >= 1
    assert any("EGFR" in req or "SERUM_CREATININE" in req for req in res.missing_requirements)


# =========================================================================
# 2. NCD RISK ENGINE ORCHESTRATION & COMPOSITE EVALUATION
# =========================================================================

def test_tc_rsk_002_engine_evaluate_model_direct():
    """TC-RSK-002: Tests NCDRiskEngine.evaluate_model() with selected model keys.
    Verifies that removing essential inputs returns INSUFFICIENT_DATA without inventing a score.
    """
    # 1. Selected model: hypertension
    htn_res = ncd_risk_engine.evaluate_model("hypertension", {"DIASTOLIC_BP": 85.0})
    assert htn_res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert htn_res.score is None
    assert "SYSTOLIC_BP" in htn_res.missing_requirements

    # 2. Selected model: cardiovascular
    cvd_res = ncd_risk_engine.evaluate_model("cardiovascular", {"AGE": 50})
    assert cvd_res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert cvd_res.score is None
    assert "SYSTOLIC_BP" in cvd_res.missing_requirements

    # 3. Selected model: diabetes
    diab_res = ncd_risk_engine.evaluate_model("diabetes", {"AGE": 50})
    assert diab_res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert diab_res.score is None
    assert any("Glycemic" in r for r in diab_res.missing_requirements)


def test_tc_rsk_002_modular_engine_composite_insufficient_data():
    """TC-RSK-002: When composite assessment is requested but essential inputs for all
    models are absent, overall_category MUST be INSUFFICIENT_DATA, overall_score MUST be None,
    and missing_requirements MUST aggregate across domains.
    """
    empty_inputs = {"SEX": "MALE"}
    assessment = ncd_risk_engine.evaluate(empty_inputs)

    assert assessment.overall_category == RiskCategory.INSUFFICIENT_DATA
    assert assessment.overall_score is None  # Does NOT invent a score!
    assert len(assessment.missing_requirements) >= 3
    assert "Insufficient clinical observations available" in assessment.clinical_summary


# =========================================================================
# 3. REST API ENDPOINTS: TC-RSK-002 INSUFFICIENT DATA VERIFICATION
# =========================================================================

def test_tc_rsk_002_api_evaluate_selected_model_endpoint(auth_tokens):
    """TC-RSK-002: Verifies POST /api/v1/risk/evaluate-model/{model_key} endpoint.
    Step: Submit payload with essential input removed.
    Expected: HTTP 200, status INSUFFICIENT_DATA, score is null, missing requirements identified.
    """
    token = auth_tokens["clinician"]

    # 1. Select hypertension model, omit SYSTOLIC_BP
    res_htn = client.post(
        "/api/v1/risk/evaluate-model/hypertension",
        json={"DIASTOLIC_BP": 92.0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_htn.status_code == 200
    data_htn = res_htn.json()
    assert data_htn["risk_category"] == "INSUFFICIENT_DATA"
    assert data_htn["score"] is None
    assert "SYSTOLIC_BP" in data_htn["missing_requirements"]

    # 2. Select cardiovascular model, omit AGE
    res_cvd = client.post(
        "/api/v1/risk/evaluate-model/cardiovascular",
        json={"SYSTOLIC_BP": 150.0, "TOTAL_CHOLESTEROL": 210.0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_cvd.status_code == 200
    data_cvd = res_cvd.json()
    assert data_cvd["risk_category"] == "INSUFFICIENT_DATA"
    assert data_cvd["score"] is None
    assert "AGE" in data_cvd["missing_requirements"]

    # 3. Select diabetes model, omit glycemic markers
    res_diab = client.post(
        "/api/v1/risk/evaluate-model/diabetes",
        json={"AGE": 55},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_diab.status_code == 200
    data_diab = res_diab.json()
    assert data_diab["risk_category"] == "INSUFFICIENT_DATA"
    assert data_diab["score"] is None
    assert len(data_diab["missing_requirements"]) >= 1


def test_tc_rsk_002_api_evaluate_custom_insufficient_data(auth_tokens):
    """TC-RSK-002: Verifies POST /api/v1/risk/evaluate-custom with missing essential inputs.
    Expected: overall_category is INSUFFICIENT_DATA, overall_score is null, missing requirements identified.
    """
    token = auth_tokens["clinician"]
    res = client.post(
        "/api/v1/risk/evaluate-custom",
        json={"SMOKING": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["overall_category"] == "INSUFFICIENT_DATA"
    assert data["overall_score"] is None
    assert len(data["missing_requirements"]) >= 3


def test_tc_rsk_002_api_citizen_evaluation_without_screening_data(auth_tokens):
    """TC-RSK-002: Verifies POST /api/v1/risk/evaluate/{citizen_id} when a synthetic citizen
    has no recorded observations and no completed screening session.
    Expected:
    - Status: INSUFFICIENT_DATA
    - overall_score: null (no invented score)
    - missing_requirements: identifies observations or screening required
    """
    token = auth_tokens["clinician"]

    # Precondition: Register a synthetic citizen with NO clinical observations
    cit_id = f"cit-rsk002-empty-{uuid.uuid4().hex[:6]}"
    citizen = CitizenRecord(
        id=cit_id,
        tenant_id="tenant-delhi-01",
        user_id=f"user-{cit_id}",
        abha_id=f"91-0000-0000-{uuid.uuid4().hex[:4]}",
        first_name="Anita",
        last_name="Verma",
        birth_date="1982-04-12",
        gender=Gender.FEMALE,
        phone="+919876543299",
        state="Delhi",
        district="Central Delhi",
        sub_district="Civil Lines",
        village_or_ward="Ward-12",
    )
    store.add_citizen(citizen)

    # Establish active consent directive
    consent = ConsentDirective(
        id=f"consent-{cit_id}",
        citizen_id=cit_id,
        grantee_id="doc-rsk-002",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
    )
    store.add_consent(consent)

    # Request risk assessment
    res = client.post(
        f"/api/v1/risk/evaluate/{cit_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()

    # 1. Status is INSUFFICIENT_DATA
    assert data["overall_tier"] == RiskTier.INSUFFICIENT_DATA.value
    # 2. Score is NOT invented
    assert data["overall_score"] is None
    assert data["domains"]["diabetes_risk"] is None
    assert data["domains"]["hypertension_risk"] is None
    # 3. Missing requirements identified
    assert len(data["missing_requirements"]) >= 1
    assert any("observations" in r.lower() or "screening" in r.lower() for r in data["missing_requirements"])
