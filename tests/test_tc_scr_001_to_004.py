"""Screening and Clinical Data Automated Test Suite (TC-SCR-001 through TC-SCR-004).

Covers:
- TC-SCR-001: Complete screening (P0, SYS-SCR-001 through SYS-SCR-005)
- TC-SCR-002: Missing required input (P0)
- TC-SCR-003: Invalid blood-pressure input (P0)
- TC-SCR-004: Unit normalization (P0)
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord
from packages.types.enums import Gender, UserRole
from packages.clinical_models.screening import ScreeningSessionCreate
from packages.clinical_models.observations import VitalsPayload
from packages.clinical_models.unit_normalization import UnitNormalizer
from packages.auth.jwt import create_access_token

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"


@pytest.fixture(autouse=True)
def setup_test_data():
    """Seed test citizen records and test users."""
    # Create synthetic citizen
    cit = CitizenRecord(
        id="citizen-synthetic-scr-01",
        tenant_id=TENANT_ID,
        user_id="user-ananya-01",
        abha_id="ABHA-1234-5678-9012",
        first_name="Ananya",
        last_name="Rao",
        birth_date="1982-04-15",
        gender=Gender.FEMALE,
        phone="+91-98888-00001",
        state="Karnataka",
        district="Bengaluru Rural",
        sub_district="Devanahalli",
        village_or_ward="Ward-12",
    )
    store.add_citizen(cit)

    # Health worker token
    worker_token = create_access_token(
        subject="worker-asha-01",
        tenant_id=TENANT_ID,
        role=UserRole.HEALTH_WORKER,
        scopes=["screening:create", "screening:read", "citizen:read"],
    )
    return {
        "citizen": cit,
        "worker_token": worker_token,
    }


# ==============================================================================
# TC-SCR-001: Complete Screening (P0)
# ==============================================================================

def test_tc_scr_001_complete_configurable_screening(setup_test_data):
    """TC-SCR-001: Complete screening using dynamic configurable questionnaire.
    Verifies:
    1. The screening is saved.
    2. Values and units are correct in discrete LOINC observations.
    3. Measurement and recording timestamps are retained.
    4. Screening status becomes COMPLETED.
    5. Applicable questionnaire version is recorded (1.2.0).
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    payload = {
        "citizen_id": citizen_id,
        "workflow_id": "mvp_comprehensive",
        "answers": {
            "age": 42,
            "sex": "FEMALE",
            "height": 162.0,
            "weight": 65.5,
            "waist_circumference": 84.0,
            "systolic_bp": 128.0,
            "diastolic_bp": 82.0,
            "heart_rate": 76.0,
            "fasting_glucose": 105.0,
            "hba1c": 5.9,
            "total_cholesterol": 195.0,
            "ldl_cholesterol": 120.0,
            "hdl_cholesterol": 48.0,
            "triglycerides": 145.0,
            "serum_creatinine": 0.85,
            "physical_activity": "Moderate",
            "diet_quality": "Moderate mixed diet",
            "sleep_hours": 7.0,
            "smoking": "NEVER",
            "alcohol": "NONE",
            "stress_level": 4.0,
            "family_history": ["DIABETES_ONE_PARENT"],
            "known_conditions": ["NONE"],
            "medication_history": ["NONE"],
        },
        "notes": "Annual community health camp screening",
        "screening_location": "VILLAGE_SUB_CENTER",
    }

    res = client.post(
        "/api/v1/screening/evaluate-configurable",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, f"Screening evaluation failed: {res.text}"
    data = res.json()

    # 1. Status is complete
    assert data["status"] == "COMPLETED"

    # 2. Questionnaire version is recorded
    assert data["questionnaire_version"] == "1.2.0"

    # 3. Timestamp retained
    assert "conducted_at" in data
    conducted_at = datetime.fromisoformat(data["conducted_at"].replace("Z", "+00:00"))
    assert (datetime.now(timezone.utc) - conducted_at).total_seconds() < 60

    # 4. Values and units are correct in discrete LOINC observations
    obs_list = data["structured_observations"]
    assert len(obs_list) >= 10

    obs_by_code = {o["code"]: o for o in obs_list}
    assert "SYSTOLIC_BP" in obs_by_code
    assert obs_by_code["SYSTOLIC_BP"]["value"] == 128.0
    assert obs_by_code["SYSTOLIC_BP"]["unit"] == "mmHg"
    assert obs_by_code["SYSTOLIC_BP"]["loinc_code"] == "8480-6"
    assert "recorded_at" in obs_by_code["SYSTOLIC_BP"]

    assert "HEIGHT" in obs_by_code
    assert obs_by_code["HEIGHT"]["value"] == 162.0
    assert obs_by_code["HEIGHT"]["unit"] == "cm"

    assert "WEIGHT" in obs_by_code
    assert obs_by_code["WEIGHT"]["value"] == 65.5
    assert obs_by_code["WEIGHT"]["unit"] == "kg"

    assert "FASTING_GLUCOSE" in obs_by_code
    assert obs_by_code["FASTING_GLUCOSE"]["value"] == 105.0
    assert obs_by_code["FASTING_GLUCOSE"]["unit"] == "mg/dL"

    # 5. Screening is saved in persistent store
    session_id = data["screening_session_id"]
    saved_screening = store.get_screening(session_id)
    assert saved_screening is not None
    assert saved_screening.status == "COMPLETED"
    assert saved_screening.questionnaire_version == "1.2.0"
    assert saved_screening.citizen_id == citizen_id


def test_tc_scr_001_complete_classic_screening_session(setup_test_data):
    """TC-SCR-001: Complete screening via standard screening session endpoint.
    Verifies status, timestamps, units, and persistent storage.
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    payload = {
        "citizen_id": citizen_id,
        "vitals": {
            "systolic_bp": 120.0,
            "diastolic_bp": 80.0,
            "heart_rate": 72.0,
            "fasting_glucose": 95.0,
            "hba1c": 5.4,
            "bmi": 22.5,
            "waist_circumference": 78.0,
        },
        "notes": "Routine PHC checkup",
        "status": "COMPLETED",
        "questionnaire_version": "1.0.0",
    }

    res = client.post(
        "/api/v1/screening/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    session_data = res.json()

    assert session_data["status"] == "COMPLETED"
    assert session_data["questionnaire_version"] == "1.0.0"
    assert "conducted_at" in session_data
    assert session_data["citizen_id"] == citizen_id

    # Verify saved in store
    saved = store.get_screening(session_data["id"])
    assert saved is not None
    assert saved.status == "COMPLETED"
    assert saved.questionnaire_version == "1.0.0"


# ==============================================================================
# TC-SCR-002: Missing Required Input (P0)
# ==============================================================================

def test_tc_scr_002_missing_required_input_rejected_with_422(setup_test_data):
    """TC-SCR-002: Omit required fields from a complete screening submission.
    Expected: System identifies missing fields and prevents invalid complete submission (422).
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    # Omit required fields: 'systolic_bp', 'diastolic_bp', 'height', 'weight'
    incomplete_answers = {
        "age": 45,
        "sex": "MALE",
        # Missing height, weight, waist_circumference, systolic_bp, diastolic_bp, etc.
    }

    payload = {
        "citizen_id": citizen_id,
        "workflow_id": "mvp_comprehensive",
        "answers": incomplete_answers,
        "allow_incomplete": False,
    }

    res = client.post(
        "/api/v1/screening/evaluate-configurable",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    err_detail = res.json()["detail"]
    assert "Missing required screening field" in err_detail
    assert "systolic_bp" in err_detail or "height" in err_detail


def test_tc_scr_002_missing_required_input_incomplete_state_when_allowed(setup_test_data):
    """TC-SCR-002: When allow_incomplete=True, system records a clearly defined
    incomplete state without silently performing complete risk calculations.
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    payload = {
        "citizen_id": citizen_id,
        "workflow_id": "mvp_comprehensive",
        "answers": {
            "age": 45,
            "sex": "MALE",
        },
        "allow_incomplete": True,
    }

    res = client.post(
        "/api/v1/screening/evaluate-configurable",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "INCOMPLETE"
    assert len(data["missing_fields"]) > 0
    assert "systolic_bp" in data["missing_fields"]
    assert "height" in data["missing_fields"]
    # No complete risk calculations performed
    assert data["risk_assessment"] is None
    assert data["explainable_summary"] is None


def test_tc_scr_002_empty_classic_screening_rejected_with_422(setup_test_data):
    """TC-SCR-002: Submitting an empty screening session without any clinical components is rejected."""
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    payload = {
        "citizen_id": citizen_id,
        # No vitals, no cbac, no idrs
    }

    res = client.post(
        "/api/v1/screening/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    assert "Missing required screening data" in res.json()["detail"]


# ==============================================================================
# TC-SCR-003: Invalid Blood-Pressure Input (P0)
# ==============================================================================

def test_tc_scr_003_negative_blood_pressure_rejected(setup_test_data):
    """TC-SCR-003: Enter negative blood-pressure value.
    Expected: Rejected per clinical validation rules; no risk calculation performed.
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    # Valid profile answers except negative systolic_bp
    answers = {
        "age": 48,
        "sex": "MALE",
        "height": 170.0,
        "weight": 75.0,
        "waist_circumference": 88.0,
        "systolic_bp": -120.0,  # NEGATIVE
        "diastolic_bp": 80.0,
        "physical_activity": "Moderate",
        "diet_quality": "Moderate mixed diet",
        "sleep_hours": 7.0,
        "smoking": "NEVER",
        "alcohol": "NONE",
        "stress_level": 5.0,
        "family_history": ["NONE"],
    }

    payload = {
        "citizen_id": citizen_id,
        "workflow_id": "mvp_comprehensive",
        "answers": answers,
    }

    res = client.post(
        "/api/v1/screening/evaluate-configurable",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    err_detail = res.json()["detail"]
    assert "Invalid systolic blood pressure" in err_detail or "rejected" in err_detail.lower()


def test_tc_scr_003_physiologically_impossible_blood_pressure(setup_test_data):
    """TC-SCR-003: Enter systolic BP less than diastolic BP or extreme values.
    Expected: Rejected per clinical bounds; no risk calculation performed.
    """
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    # SBP <= DBP (impossible physiology: SBP=80, DBP=120)
    answers = {
        "age": 48,
        "sex": "MALE",
        "height": 170.0,
        "weight": 75.0,
        "waist_circumference": 88.0,
        "systolic_bp": 80.0,
        "diastolic_bp": 120.0,  # DBP higher than SBP!
        "physical_activity": "Moderate",
        "diet_quality": "Moderate mixed diet",
        "sleep_hours": 7.0,
        "smoking": "NEVER",
        "alcohol": "NONE",
        "stress_level": 5.0,
        "family_history": ["NONE"],
    }

    res = client.post(
        "/api/v1/screening/evaluate-configurable",
        json={"citizen_id": citizen_id, "workflow_id": "mvp_comprehensive", "answers": answers},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    assert "systolic" in res.json()["detail"].lower() and "diastolic" in res.json()["detail"].lower()


def test_tc_scr_003_vitals_payload_validation_rejects_invalid_bp(setup_test_data):
    """TC-SCR-003: Direct VitalsPayload submission with invalid BP rejected at model layer."""
    token = setup_test_data["worker_token"]
    citizen_id = setup_test_data["citizen"].id

    payload = {
        "citizen_id": citizen_id,
        "vitals": {
            "systolic_bp": -130.0,  # Negative
            "diastolic_bp": 80.0,
        },
    }

    res = client.post(
        "/api/v1/screening/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422
    assert "systolic blood pressure" in res.text.lower()


# ==============================================================================
# TC-SCR-004: Unit Normalization (P0)
# ==============================================================================

def test_tc_scr_004_height_normalization():
    """TC-SCR-004: Height in inches vs centimeters.
    67.0 inches ~ 170.18 cm. Both normalize to cm within tolerance.
    """
    # Direct normalization
    norm_cm = UnitNormalizer.normalize("HEIGHT", 170.2, "cm")
    norm_in = UnitNormalizer.normalize("HEIGHT", 67.0, "in")

    assert norm_cm.canonical_unit == "cm"
    assert norm_in.canonical_unit == "cm"
    assert abs(norm_cm.normalized_value - norm_in.normalized_value) <= 0.5

    # Equivalence check
    eq, n1, n2 = UnitNormalizer.are_equivalent("HEIGHT", 170.2, "cm", 67.0, "in", rounding_tolerance=0.5)
    assert eq is True


def test_tc_scr_004_weight_normalization():
    """TC-SCR-004: Weight in pounds vs kilograms.
    154.32 lbs ~ 70.0 kg.
    """
    norm_kg = UnitNormalizer.normalize("WEIGHT", 70.0, "kg")
    norm_lb = UnitNormalizer.normalize("WEIGHT", 154.32, "lbs")

    assert norm_kg.canonical_unit == "kg"
    assert norm_lb.canonical_unit == "kg"
    assert abs(norm_kg.normalized_value - norm_lb.normalized_value) <= 0.5

    eq, _, _ = UnitNormalizer.are_equivalent("WEIGHT", 70.0, "kg", 154.32, "lbs")
    assert eq is True


def test_tc_scr_004_glucose_normalization():
    """TC-SCR-004: Fasting Blood Glucose in mg/dL vs mmol/L.
    5.55 mmol/L ~ 100.0 mg/dL (conversion factor ~ 18.0182).
    """
    norm_mgdl = UnitNormalizer.normalize("FASTING_GLUCOSE", 100.0, "mg/dL")
    norm_mmol = UnitNormalizer.normalize("FASTING_GLUCOSE", 5.55, "mmol/L")

    assert norm_mgdl.canonical_unit == "mg/dL"
    assert norm_mmol.canonical_unit == "mg/dL"
    assert abs(norm_mgdl.normalized_value - norm_mmol.normalized_value) <= 0.5

    eq, _, _ = UnitNormalizer.are_equivalent("FASTING_GLUCOSE", 100.0, "mg/dL", 5.55, "mmol/L")
    assert eq is True


def test_tc_scr_004_blood_pressure_normalization():
    """TC-SCR-004: Blood Pressure in kPa vs mmHg.
    16.0 kPa ~ 120.0 mmHg.
    """
    norm_mmhg = UnitNormalizer.normalize("SYSTOLIC_BP", 120.0, "mmHg")
    norm_kpa = UnitNormalizer.normalize("SYSTOLIC_BP", 16.0, "kPa")

    assert norm_mmhg.canonical_unit == "mmHg"
    assert norm_kpa.canonical_unit == "mmHg"
    assert abs(norm_mmhg.normalized_value - norm_kpa.normalized_value) <= 0.5

    eq, _, _ = UnitNormalizer.are_equivalent("SYSTOLIC_BP", 120.0, "mmHg", 16.0, "kPa")
    assert eq is True


def test_tc_scr_004_api_endpoints_normalization_and_comparison():
    """TC-SCR-004: Verifies the REST API endpoints for unit normalization and comparison."""
    # 1. Normalize measurement endpoint
    res = client.post(
        "/api/v1/screening/normalize-measurement",
        json={"metric": "HEIGHT", "value": 70.0, "unit": "inches"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["canonical_unit"] == "cm"
    assert data["normalized_value"] == 177.8

    # 2. Compare measurements endpoint (equivalent within tolerance)
    res_cmp = client.post(
        "/api/v1/screening/compare-measurements",
        json={
            "metric": "FASTING_GLUCOSE",
            "value1": 108.0,
            "unit1": "mg/dL",
            "value2": 5.99,
            "unit2": "mmol/L",
            "tolerance": 0.5,
        },
    )
    assert res_cmp.status_code == 200
    cmp_data = res_cmp.json()
    assert cmp_data["equivalent"] is True
    assert cmp_data["canonical_unit"] == "mg/dL"
    assert abs(cmp_data["normalized_value1"] - cmp_data["normalized_value2"]) <= 0.5
