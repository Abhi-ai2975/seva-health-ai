"""TC-RSK-005: Input boundary validation
Priority: P0
Requirement: SYS-RSK-001 through SYS-RSK-006

Steps:
1. Submit measurements at documented valid boundaries.
2. Submit values immediately outside those boundaries.
3. Observe validation and model behavior.

Expected result:
- Boundary behavior matches the documented input specification.
- Unsupported values do NOT silently produce misleading assessments.
- Values exactly on documented boundaries are accepted and assessed safely.
- Values immediately outside documented boundaries are strictly rejected with
  PhysiologicalValidationError (Python) and HTTP 422 Unprocessable Entity (REST API).
"""

import pytest
from starlette.testclient import TestClient

from services.api.main import app
from packages.auth.jwt import create_access_token
from packages.types.enums import UserRole
from services.risk_engine.base import (
    PhysiologicalRangeValidator,
    PhysiologicalValidationError,
    RiskCategory,
)
from services.risk_engine.models.hypertension import HypertensionRiskModel
from services.risk_engine.models.diabetes import DiabetesRiskModel
from services.risk_engine.models.cardiovascular import CardiovascularRiskModel
from services.risk_engine.engine import ncd_risk_engine

client = TestClient(app)

TENANT_ID = "tenant-delhi-01"


@pytest.fixture
def auth_tokens():
    """Generates valid JWT tokens for required personas."""
    return {
        "clinician": create_access_token(
            subject="doc-rsk-005",
            tenant_id=TENANT_ID,
            role=UserRole.CLINICIAN,
            scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
        ),
    }


# =========================================================================
# 1. DOCUMENTED BOUNDARY VALIDATION SPECIFICATION TESTS
# =========================================================================

@pytest.mark.parametrize("metric,min_val,max_val,unit", [
    ("SYSTOLIC_BP", 60.0, 280.0, "mmHg"),
    ("DIASTOLIC_BP", 30.0, 160.0, "mmHg"),
    ("FASTING_GLUCOSE", 35.0, 600.0, "mg/dL"),
    ("HBA1C", 3.0, 20.0, "%"),
    ("TOTAL_CHOLESTEROL", 50.0, 600.0, "mg/dL"),
    ("AGE", 1.0, 125.0, "years"),
    ("WAIST_CIRCUMFERENCE", 40.0, 180.0, "cm"),
    ("BMI", 10.0, 90.0, "kg/m2"),
])
def test_tc_rsk_005_exact_boundary_values_accepted(metric, min_val, max_val, unit):
    """TC-RSK-005: Values exactly on the documented lower and upper boundaries
    MUST pass validation without error.
    """
    # Lower boundary exact value
    PhysiologicalRangeValidator.validate(metric, min_val, unit)

    # Upper boundary exact value
    PhysiologicalRangeValidator.validate(metric, max_val, unit)


@pytest.mark.parametrize("metric,below_min,above_max,unit", [
    ("SYSTOLIC_BP", 59.9, 280.1, "mmHg"),
    ("DIASTOLIC_BP", 29.9, 160.1, "mmHg"),
    ("FASTING_GLUCOSE", 34.9, 600.1, "mg/dL"),
    ("HBA1C", 2.9, 20.1, "%"),
    ("TOTAL_CHOLESTEROL", 49.9, 600.1, "mg/dL"),
    ("AGE", 0.9, 125.1, "years"),
    ("WAIST_CIRCUMFERENCE", 39.9, 180.1, "cm"),
    ("BMI", 9.9, 90.1, "kg/m2"),
])
def test_tc_rsk_005_values_immediately_outside_boundaries_rejected(metric, below_min, above_max, unit):
    """TC-RSK-005: Values immediately outside the documented boundaries (even by 0.1 units)
    MUST raise PhysiologicalValidationError and NOT silently calculate risk.
    """
    # Test immediately below minimum boundary
    with pytest.raises(PhysiologicalValidationError) as exc_min:
        PhysiologicalRangeValidator.validate(metric, below_min, unit)
    assert exc_min.value.metric == metric
    assert exc_min.value.value == below_min

    # Test immediately above maximum boundary
    with pytest.raises(PhysiologicalValidationError) as exc_max:
        PhysiologicalRangeValidator.validate(metric, above_max, unit)
    assert exc_max.value.metric == metric
    assert exc_max.value.value == above_max


# =========================================================================
# 2. DOMAIN MODEL LEVEL BOUNDARY BEHAVIOR TESTS
# =========================================================================

def test_tc_rsk_005_hypertension_model_boundary_behavior():
    """TC-RSK-005: Tests Hypertension model at documented boundaries [60, 280] / [30, 160]."""
    model = HypertensionRiskModel()

    # Documented lower boundary
    res_min = model.calculate({"SYSTOLIC_BP": 60.0, "DIASTOLIC_BP": 30.0})
    assert res_min.risk_category == RiskCategory.LOW
    assert res_min.score is not None

    # Documented upper boundary
    res_max = model.calculate({"SYSTOLIC_BP": 280.0, "DIASTOLIC_BP": 160.0})
    assert res_max.risk_category == RiskCategory.HIGH
    assert res_max.score is not None

    # Immediately below boundary (59.9 mmHg)
    with pytest.raises(PhysiologicalValidationError) as exc:
        model.calculate({"SYSTOLIC_BP": 59.9, "DIASTOLIC_BP": 75.0})
    assert "Expected physiological range is [60.0, 280.0] mmHg" in str(exc.value)

    # Immediately above boundary (280.1 mmHg)
    with pytest.raises(PhysiologicalValidationError) as exc:
        model.calculate({"SYSTOLIC_BP": 280.1, "DIASTOLIC_BP": 100.0})
    assert "Expected physiological range is [60.0, 280.0] mmHg" in str(exc.value)

    # Grossly unsupported negative value
    with pytest.raises(PhysiologicalValidationError):
        model.calculate({"SYSTOLIC_BP": -20.0, "DIASTOLIC_BP": 80.0})


def test_tc_rsk_005_diabetes_model_boundary_behavior():
    """TC-RSK-005: Tests Diabetes model at documented boundaries."""
    model = DiabetesRiskModel()

    # Boundary valid: HbA1c = 3.0%, FBG = 35.0 mg/dL
    res_min = model.calculate({"AGE": 40, "HBA1C": 3.0, "FASTING_GLUCOSE": 35.0})
    assert res_min.risk_category == RiskCategory.LOW
    assert res_min.score is not None

    # Boundary valid: HbA1c = 20.0%, FBG = 600.0 mg/dL
    res_max = model.calculate({"AGE": 55, "HBA1C": 20.0, "FASTING_GLUCOSE": 600.0})
    assert res_max.risk_category == RiskCategory.HIGH
    assert res_max.score is not None

    # Immediately below boundary: HbA1c = 2.9%
    with pytest.raises(PhysiologicalValidationError):
        model.calculate({"AGE": 40, "HBA1C": 2.9})

    # Immediately above boundary: HbA1c = 20.1%
    with pytest.raises(PhysiologicalValidationError):
        model.calculate({"AGE": 40, "HBA1C": 20.1})


def test_tc_rsk_005_engine_orchestration_boundary_protection():
    """TC-RSK-005: Verifies NCDRiskEngine.evaluate halts on unsupported boundary values."""
    # Boundary violation in multi-domain input
    unsupported_payload = {
        "AGE": 45,
        "SYSTOLIC_BP": 290.0,  # Exceeds max 280.0
        "DIASTOLIC_BP": 95.0,
        "HBA1C": 6.5,
    }
    with pytest.raises(PhysiologicalValidationError):
        ncd_risk_engine.evaluate(unsupported_payload)


# =========================================================================
# 3. REST API ENDPOINT BOUNDARY REJECTION TESTS (HTTP 422)
# =========================================================================

def test_tc_rsk_005_api_evaluate_custom_boundary_rejection(auth_tokens):
    """TC-RSK-005: Verifies that submitting values outside documented boundaries to
    POST /api/v1/risk/evaluate-custom returns HTTP 422 Unprocessable Entity with error details.
    """
    token = auth_tokens["clinician"]

    # 1. Exceeds upper systolic boundary (280.5 > 280.0)
    res_over = client.post(
        "/api/v1/risk/evaluate-custom",
        json={"SYSTOLIC_BP": 280.5, "DIASTOLIC_BP": 95.0, "AGE": 45},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_over.status_code == 422
    err_over = res_over.json()
    assert err_over["error_type"] == "PHYSIOLOGICAL_VALIDATION_ERROR"
    assert err_over["metric"] == "SYSTOLIC_BP"
    assert err_over["value"] == 280.5
    assert err_over["allowed_max"] == 280.0

    # 2. Below lower systolic boundary (55.0 < 60.0)
    res_under = client.post(
        "/api/v1/risk/evaluate-custom",
        json={"SYSTOLIC_BP": 55.0, "DIASTOLIC_BP": 35.0, "AGE": 45},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_under.status_code == 422
    err_under = res_under.json()
    assert err_under["error_type"] == "PHYSIOLOGICAL_VALIDATION_ERROR"
    assert err_under["value"] == 55.0
    assert err_under["allowed_min"] == 60.0

    # 3. Valid boundary values are accepted with HTTP 200
    res_valid = client.post(
        "/api/v1/risk/evaluate-custom",
        json={"SYSTOLIC_BP": 280.0, "DIASTOLIC_BP": 160.0, "AGE": 120.0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["overall_score"] is not None


def test_tc_rsk_005_api_evaluate_model_endpoint_boundary_rejection(auth_tokens):
    """TC-RSK-005: Verifies that POST /api/v1/risk/evaluate-model/hypertension
    rejects unsupported boundary inputs with HTTP 422.
    """
    token = auth_tokens["clinician"]

    # Negative blood pressure
    res_neg = client.post(
        "/api/v1/risk/evaluate-model/hypertension",
        json={"SYSTOLIC_BP": -5.0, "DIASTOLIC_BP": 80.0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_neg.status_code == 422
    data_neg = res_neg.json()
    assert data_neg["error_type"] == "PHYSIOLOGICAL_VALIDATION_ERROR"
    assert "Expected physiological range" in data_neg["detail"]
