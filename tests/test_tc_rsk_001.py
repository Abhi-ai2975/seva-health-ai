"""Test Suite for TC-RSK-001: Valid Risk Assessment (Priority: P0, SYS-RSK-001 through SYS-RSK-006).

Steps:
1. Prepare a reference input set supported by the configured model.
2. Submit an assessment request.
3. Retrieve the result.

Expected result:
- The assessment completes.
- The correct model and version are identified.
- The input snapshot or immutable reference is preserved.
- The output matches the approved reference case within the specified tolerance.
- Relevant contributors and limitations are available.
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, RiskTier, TrajectoryTrend
from packages.clinical_models.observations import Observation
from packages.clinical_models.screening import ScreeningSession, VitalsPayload
from packages.auth.jwt import create_access_token
from services.risk_engine.engine import ncd_risk_engine, NCDRiskEngine
from services.risk_engine.base import RiskCategory, ImpactDirection
from services.risk_engine.models.diabetes import DiabetesRiskModel
from services.risk_engine.models.hypertension import HypertensionRiskModel
from services.risk_engine.models.cardiovascular import CardiovascularRiskModel

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"


@pytest.fixture
def auth_tokens():
    """Create test tokens for clinician and health worker."""
    clinician_token = create_access_token(
        subject="clinician-dr-sharma",
        tenant_id=TENANT_ID,
        role=UserRole.CLINICIAN,
        scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
    )
    worker_token = create_access_token(
        subject="worker-asha-01",
        tenant_id=TENANT_ID,
        role=UserRole.HEALTH_WORKER,
        scopes=["screening:create", "screening:read", "risk:read", "risk:create"],
    )
    return {
        "clinician": clinician_token,
        "worker": worker_token,
    }


# ==============================================================================
# APPROVED REFERENCE DATA SETS & BENCHMARK GROUND TRUTHS
# ==============================================================================

# Reference Case A: High-Risk Metabolic & Vascular Patient (ICMR-INDIAB & ACC/AHA Benchmark)
REFERENCE_CASE_HIGH = {
    "AGE": 52,
    "SEX": "MALE",
    "SYSTOLIC_BP": 160.0,
    "DIASTOLIC_BP": 98.0,
    "HBA1C": 7.8,
    "FASTING_GLUCOSE": 145.0,
    "TOTAL_CHOLESTEROL": 230.0,
    "HDL_CHOLESTEROL": 38.0,
    "TRIGLYCERIDES": 200.0,
    "WAIST_CIRCUMFERENCE": 98.0,
    "BMI": 28.5,
    "SMOKING": True,
    "EGFR": 68.0,
}
# Approved benchmark outputs for Reference Case A
BENCHMARK_HIGH = {
    "overall_score": 0.77,
    "tolerance": 0.02,
    "overall_category": RiskCategory.HIGH,
    "diabetes_score": 0.88,
    "hypertension_score": 0.92,
    "cardiovascular_score": 0.55,
    "obesity_score": 0.95,
    "ckd_score": 0.40,
}

# Reference Case B: Optimal Low-Risk Healthy Patient
REFERENCE_CASE_LOW = {
    "AGE": 28,
    "SEX": "FEMALE",
    "SYSTOLIC_BP": 112.0,
    "DIASTOLIC_BP": 72.0,
    "HBA1C": 5.1,
    "FASTING_GLUCOSE": 86.0,
    "TOTAL_CHOLESTEROL": 165.0,
    "HDL_CHOLESTEROL": 55.0,
    "TRIGLYCERIDES": 95.0,
    "WAIST_CIRCUMFERENCE": 72.0,
    "BMI": 21.0,
    "SMOKING": False,
    "EGFR": 105.0,
}
# Approved benchmark outputs for Reference Case B
BENCHMARK_LOW = {
    "overall_score": 0.098,
    "tolerance": 0.02,
    "overall_category": RiskCategory.LOW,
    "diabetes_score": 0.15,
    "hypertension_score": 0.10,
    "cardiovascular_score": 0.05,
    "obesity_score": 0.10,
    "ckd_score": 0.08,
}


# ==============================================================================
# TEST 1: APPROVED DOMAIN MODELS VALIDATION (TC-RSK-001)
# ==============================================================================

def test_tc_rsk_001_diabetes_model_reference_case():
    """TC-RSK-001: Validates the ICMR-INDIAB Diabetes Risk Model against approved reference set.
    Verifies:
    1. Correct model and version are identified ('ICMR-INDIAB-IDRS-2023.v1').
    2. Input snapshot is preserved.
    3. Output score matches approved reference benchmark (0.88) within tolerance (+/- 0.02).
    4. Relevant contributors and limitations are available.
    """
    model = DiabetesRiskModel()

    # 1. Model & Version Identified
    assert model.domain == "Diabetes & Glycemic Risk"
    assert model.version() == "ICMR-INDIAB-IDRS-2023.v1"
    assert model.is_clinically_validated() is True
    assert "ICMR" in model.provenance()["authority"]

    # 2. Assessment execution with reference input set
    inputs = {
        "AGE": REFERENCE_CASE_HIGH["AGE"],
        "HBA1C": REFERENCE_CASE_HIGH["HBA1C"],
        "FASTING_GLUCOSE": REFERENCE_CASE_HIGH["FASTING_GLUCOSE"],
        "WAIST_CIRCUMFERENCE": REFERENCE_CASE_HIGH["WAIST_CIRCUMFERENCE"],
        "SEX": REFERENCE_CASE_HIGH["SEX"],
    }
    result = model.calculate(inputs)

    # 3. Assessment Completes
    assert result is not None
    assert result.model_version == "ICMR-INDIAB-IDRS-2023.v1"

    # 4. Input snapshot preserved
    assert "HBA1C" in result.input_snapshot
    assert result.input_snapshot["HBA1C"] == 7.8
    assert result.input_snapshot["FASTING_GLUCOSE"] == 145.0

    # 5. Output matches approved reference case within tolerance (+/- 0.02)
    assert abs(result.score - BENCHMARK_HIGH["diabetes_score"]) <= 0.02
    assert result.risk_category == RiskCategory.HIGH

    # 6. Relevant contributors & limitations are available
    assert len(result.risk_factor_contributions) >= 2
    top_driver = result.risk_factor_contributions[0]
    assert "Diabetic" in top_driver.feature
    assert top_driver.impact_weight > 0.20
    assert top_driver.evidence_citation != ""

    assert len(result.limitations) >= 2
    assert any("OGTT" in lim or "glucometer" in lim.lower() for lim in result.limitations)
    assert "SAFETY NOTICE" in result.clinical_safety_notice


def test_tc_rsk_001_hypertension_model_reference_case():
    """TC-RSK-001: Validates the ACC/AHA & ICMR Hypertension Risk Model against approved reference set.
    Verifies model, version, input snapshot preservation, benchmark score match, and contributors.
    """
    model = HypertensionRiskModel()

    assert model.domain == "Hypertension & Vascular Risk"
    assert model.version() == "ACC-AHA-2017-ICMR.v1"

    inputs = {
        "SYSTOLIC_BP": REFERENCE_CASE_HIGH["SYSTOLIC_BP"],
        "DIASTOLIC_BP": REFERENCE_CASE_HIGH["DIASTOLIC_BP"],
    }
    result = model.calculate(inputs)

    assert result.model_version == "ACC-AHA-2017-ICMR.v1"
    assert result.input_snapshot["SYSTOLIC_BP"] == 160.0
    assert result.input_snapshot["DIASTOLIC_BP"] == 98.0

    # Benchmark score match within tolerance
    assert abs(result.score - BENCHMARK_HIGH["hypertension_score"]) <= 0.02
    assert result.risk_category == RiskCategory.HIGH

    # Relevant contributors and limitations
    assert len(result.risk_factor_contributions) >= 1
    assert "Stage 2" in result.risk_factor_contributions[0].feature
    assert len(result.limitations) >= 1


def test_tc_rsk_001_cardiovascular_model_reference_case():
    """TC-RSK-001: Validates the WHO-SEAR 10-Year ASCVD Risk Model against approved reference set."""
    model = CardiovascularRiskModel()

    assert model.domain == "Cardiovascular 10-Year ASCVD Risk"
    assert model.version() == "WHO-SEAR-ASCVD-2019.v1"

    inputs = {
        "AGE": 68,
        "SYSTOLIC_BP": 158.0,
        "TOTAL_CHOLESTEROL": 250.0,
        "SMOKING": True,
        "SEX": "MALE",
    }
    result = model.calculate(inputs)

    assert result.model_version == "WHO-SEAR-ASCVD-2019.v1"
    assert result.input_snapshot["SYSTOLIC_BP"] == 158.0
    assert bool(result.input_snapshot["SMOKING"]) is True

    # Output matches approved reference case (0.73 +/- 0.02)
    assert abs(result.score - 0.73) <= 0.02
    assert result.risk_category == RiskCategory.HIGH

    # Contributors and limitations
    assert any("Tobacco" in c.feature for c in result.risk_factor_contributions)
    assert len(result.limitations) >= 1


# ==============================================================================
# TEST 2: COMPOSITE MODULAR ENGINE REFERENCE EVALUATION (API & ENGINE)
# ==============================================================================

def test_tc_rsk_001_modular_engine_composite_high_risk_reference_case():
    """TC-RSK-001: Submits full reference set to composite modular risk engine.
    Verifies:
    1. Composite assessment completes.
    2. Overall model ('NCD_MODULAR_RISK_ENGINE') and version ('calibrated_ensemble_v1.0.0') are identified.
    3. Input snapshot is preserved in its entirety.
    4. Composite score matches reference benchmark within tolerance (0.77 +/- 0.02).
    5. Domain scores match reference benchmarks.
    6. Ranked contributors and limitations are available.
    """
    assessment = ncd_risk_engine.evaluate(raw_inputs=REFERENCE_CASE_HIGH)

    # 1. Assessment completes
    assert assessment is not None

    # 2. Correct model and version identified
    assert assessment.model_name == "NCD_MODULAR_RISK_ENGINE"
    assert assessment.model_version == "calibrated_ensemble_v1.0.0"

    # 3. Input snapshot preserved
    assert assessment.input_snapshot["AGE"] == 52
    assert assessment.input_snapshot["HBA1C"] == 7.8
    assert assessment.input_snapshot["SYSTOLIC_BP"] == 160.0

    # 4. Output matches approved reference case within tolerance (+/- 0.02)
    assert abs(assessment.overall_score - BENCHMARK_HIGH["overall_score"]) <= BENCHMARK_HIGH["tolerance"]
    assert assessment.overall_category == BENCHMARK_HIGH["overall_category"]

    # 5. Domain results match reference benchmarks
    doms = assessment.domain_results
    assert abs(doms["diabetes"].score - BENCHMARK_HIGH["diabetes_score"]) <= 0.02
    assert abs(doms["hypertension"].score - BENCHMARK_HIGH["hypertension_score"]) <= 0.02
    assert abs(doms["cardiovascular"].score - BENCHMARK_HIGH["cardiovascular_score"]) <= 0.05
    assert abs(doms["obesity"].score - BENCHMARK_HIGH["obesity_score"]) <= 0.02
    assert abs(doms["ckd"].score - BENCHMARK_HIGH["ckd_score"]) <= 0.02

    # 6. Relevant contributors and limitations available
    assert len(assessment.top_drivers) >= 3
    # Top drivers must be sorted descending by impact_weight
    assert assessment.top_drivers[0].impact_weight >= assessment.top_drivers[1].impact_weight

    assert len(assessment.limitations) >= 3
    assert len(assessment.recommended_next_steps) >= 3
    assert "SAFETY NOTICE" in assessment.clinical_safety_notice


def test_tc_rsk_001_modular_engine_composite_low_risk_reference_case():
    """TC-RSK-001: Submits optimal low-risk reference set to composite modular risk engine.
    Verifies output matches low-risk benchmark (< 0.12) within tolerance and yields protective factors.
    """
    assessment = ncd_risk_engine.evaluate(raw_inputs=REFERENCE_CASE_LOW)

    assert assessment.model_name == "NCD_MODULAR_RISK_ENGINE"
    assert assessment.model_version == "calibrated_ensemble_v1.0.0"
    assert assessment.input_snapshot["HBA1C"] == 5.1

    # Output matches approved benchmark (0.098 +/- 0.02)
    assert abs(assessment.overall_score - BENCHMARK_LOW["overall_score"]) <= BENCHMARK_LOW["tolerance"]
    assert assessment.overall_category == RiskCategory.LOW

    # Protective factors identified
    assert len(assessment.protective_factors) >= 2
    assert any(p.impact_direction == ImpactDirection.DECREASES_RISK for p in assessment.protective_factors)


# ==============================================================================
# TEST 3: REST API EVALUATION & IMMUTABLE REFERENCE PRESERVATION
# ==============================================================================

def test_tc_rsk_001_api_evaluate_custom_reference_case(auth_tokens):
    """TC-RSK-001: Evaluates reference input set via REST API endpoint POST /api/v1/risk/evaluate-custom.
    Verifies end-to-end HTTP pipeline completes and preserves model provenance, version, and snapshot.
    """
    token = auth_tokens["clinician"]

    res = client.post(
        "/api/v1/risk/evaluate-custom",
        json=REFERENCE_CASE_HIGH,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # 1. Assessment completes
    assert data["overall_category"] == "HIGH"

    # 2. Correct model and version identified
    assert data["model_name"] == "NCD_MODULAR_RISK_ENGINE"
    assert data["model_version"] == "calibrated_ensemble_v1.0.0"

    # 3. Input snapshot preserved
    assert data["input_snapshot"]["SYSTOLIC_BP"] == 160.0
    assert data["input_snapshot"]["HBA1C"] == 7.8

    # 4. Output matches approved reference case within tolerance
    assert abs(data["overall_score"] - 0.77) <= 0.02

    # 5. Contributors and limitations available
    assert len(data["top_drivers"]) >= 3
    assert len(data["limitations"]) >= 3
    assert len(data["recommended_next_steps"]) >= 3


def test_tc_rsk_001_api_citizen_risk_evaluation_and_retrieval(auth_tokens):
    """TC-RSK-001: Register synthetic citizen, submit clinical assessment request,
    and retrieve persisted result.
    Verifies:
    1. Assessment completes and is saved in repository.
    2. Model name and version are identified ('NCD_COMPOSITE_RISK_ENGINE', 'calibrated_ensemble_v1.0.0').
    3. Immutable screening_session_id and input snapshot are preserved.
    4. Output matches reference evaluation.
    5. Relevant contributors and limitations are returned on retrieval.
    """
    token = auth_tokens["clinician"]

    # 1. Prepare synthetic citizen
    cit = CitizenRecord(
        id="citizen-synthetic-rsk-01",
        tenant_id=TENANT_ID,
        user_id="user-rsk-01",
        abha_id="ABHA-9999-8888-7777",
        first_name="Sunil",
        last_name="Verma",
        birth_date="1972-06-10",  # ~54 years old
        gender=Gender.MALE,
        phone="+91-97777-11111",
        state="Karnataka",
        district="Bengaluru Urban",
        sub_district="North",
        village_or_ward="Ward-45",
    )
    store.add_citizen(cit)

    # Grant active consent directive to Dr. Sharma for clinical risk evaluation
    consent = ConsentDirective(
        id="consent-rsk-sunil-01",
        citizen_id=cit.id,
        grantee_id="clinician-dr-sharma",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
    )
    store.add_consent(consent)

    # 2. Ingest reference observations
    obs_data = [
        ("SYSTOLIC_BP", 158.0, "mmHg", "8480-6", "Systolic BP"),
        ("DIASTOLIC_BP", 96.0, "mmHg", "8462-4", "Diastolic BP"),
        ("FASTING_GLUCOSE", 142.0, "mg/dL", "1558-6", "Fasting Glucose"),
        ("HBA1C", 7.6, "%", "4548-4", "HbA1c"),
        ("TOTAL_CHOLESTEROL", 225.0, "mg/dL", "2093-3", "Total Cholesterol"),
        ("HDL_CHOLESTEROL", 39.0, "mg/dL", "2085-9", "HDL Cholesterol"),
        ("TRIGLYCERIDES", 195.0, "mg/dL", "2571-8", "Triglycerides"),
        ("BMI", 28.0, "kg/m2", "39156-5", "BMI"),
        ("WAIST_CIRCUMFERENCE", 96.0, "cm", "8280-0", "Waist Circumference"),
    ]
    now = datetime.now(timezone.utc)
    for code, val, unit, loinc, disp in obs_data:
        obs = Observation(
            citizen_id=cit.id,
            tenant_id=TENANT_ID,
            code=code,
            value=val,
            unit=unit,
            loinc_code=loinc,
            display_name=disp,
            recorded_at=now,
        )
        store.add_observation(obs)

    # Add screening session to provide immutable screening reference
    screening = ScreeningSession(
        citizen_id=cit.id,
        tenant_id=TENANT_ID,
        conducted_by_id="worker-asha-01",
        conducted_at=now,
        status="COMPLETED",
        questionnaire_version="1.2.0",
        calculated_idrs_score=70,
        vitals=VitalsPayload(systolic_bp=158.0, diastolic_bp=96.0, hba1c=7.6),
    )
    store.add_screening(screening)

    # 3. Submit assessment request
    post_res = client.post(
        f"/api/v1/risk/evaluate/{cit.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post_res.status_code == 200, post_res.text
    assessment_data = post_res.json()

    # 4. Retrieve the result via GET /api/v1/risk/latest/{citizen_id}
    get_res = client.get(
        f"/api/v1/risk/latest/{cit.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200, get_res.text
    retrieved_data = get_res.json()

    # Assert retrieval matches submission
    assert retrieved_data["id"] == assessment_data["id"]

    # 5. Expected result verification
    # a. Assessment completes
    assert retrieved_data["overall_tier"] in ["HIGH", "CRITICAL"]
    assert retrieved_data["overall_score"] >= 0.50

    # b. Correct model and version are identified
    assert retrieved_data["model_name"] == "NCD_COMPOSITE_RISK_ENGINE"
    assert retrieved_data["model_version"] == "calibrated_ensemble_v1.0.0"

    # c. Immutable reference and input snapshot preserved
    assert retrieved_data["screening_session_id"] == screening.id
    assert retrieved_data["input_snapshot"] is not None
    assert retrieved_data["input_snapshot"]["SYSTOLIC_BP"] == 158.0
    assert retrieved_data["input_snapshot"]["HBA1C"] == 7.6

    # d. Output matches reference tolerance (high risk composite)
    assert abs(retrieved_data["overall_score"] - 0.75) <= 0.15

    # e. Relevant contributors and limitations are available
    assert len(retrieved_data["top_drivers"]) >= 2
    driver_name = retrieved_data["top_drivers"][0]["feature_name"]
    assert "Diabetic" in driver_name or "Hypertension" in driver_name or "Blood" in driver_name or "Glucose" in driver_name
    assert len(retrieved_data["limitations"]) >= 2
    assert "CLINICAL REVIEW RECOMMENDED" in retrieved_data["safety_disclaimer"]


def test_tc_rsk_001_model_catalog_endpoint(auth_tokens):
    """TC-RSK-001: Verifies the model catalog endpoint GET /api/v1/risk/models
    identifies all registered clinical models with versions, validation status, and limitations.
    """
    token = auth_tokens["clinician"]

    res = client.get("/api/v1/risk/models", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    catalog = res.json()

    assert len(catalog) >= 5
    domains = {m["key"]: m for m in catalog}

    # Verify each domain identifies model version, validation status, and limitations
    assert "diabetes" in domains
    assert domains["diabetes"]["version"] == "ICMR-INDIAB-IDRS-2023.v1"
    assert domains["diabetes"]["is_clinically_validated"] is True
    assert len(domains["diabetes"]["limitations"]) >= 2

    assert "hypertension" in domains
    assert domains["hypertension"]["version"] == "ACC-AHA-2017-ICMR.v1"
    assert domains["hypertension"]["is_clinically_validated"] is True

    assert "cardiovascular" in domains
    assert domains["cardiovascular"]["version"] == "WHO-SEAR-ASCVD-2019.v1"
    assert domains["cardiovascular"]["is_clinically_validated"] is True

    assert "obesity" in domains
    assert domains["obesity"]["version"] == "WHO-ASIAN-BMI-ICMR-2023.v1"

    assert "ckd" in domains
    assert domains["ckd"]["version"] == "KDIGO-CKD-EPI-2024.v1"
