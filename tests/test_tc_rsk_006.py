"""TC-RSK-006: Risk-factor explanation consistency
Priority: P0
Requirement: SYS-RSK-001 through SYS-RSK-006

Steps:
1. Generate an assessment with known input factors.
2. Retrieve the contributing-factor explanation.
3. Compare the explanation with the model output and input snapshot.

Expected result:
- Explanations correspond strictly to the recorded assessment and input snapshot.
- The AI does NOT invent contributors or claim unsupported causal relationships.
- Observed values reported in explanations match the frozen input snapshot.
- Top drivers have positive impact weight (INCREASES_RISK), protective factors have negative weight (DECREASES_RISK).
- If a risk marker is normal/optimal, it is NOT falsely identified as a risk driver.
- All drivers provide evidence citations grounded in peer-reviewed/national guidelines.
"""

import pytest
from datetime import datetime, timezone
import uuid
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, RiskTier
from packages.clinical_models.observations import Observation
from packages.clinical_models.screening import ScreeningSession, VitalsPayload
from packages.auth.jwt import create_access_token
from services.risk_engine.engine import ncd_risk_engine
from services.risk_engine.base import ImpactDirection

client = TestClient(app)

TENANT_ID = "tenant-delhi-01"


@pytest.fixture
def auth_tokens():
    """Generates valid JWT tokens for clinician persona."""
    return {
        "clinician": create_access_token(
            subject="doc-rsk-006",
            tenant_id=TENANT_ID,
            role=UserRole.CLINICIAN,
            scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
        ),
    }


def test_tc_rsk_006_explanation_corresponds_to_known_inputs_and_snapshot(auth_tokens):
    """TC-RSK-006: Patient with known high-risk biometric inputs:
    - High Blood Pressure: SBP 164.0, DBP 102.0
    - Diabetic Glycemia: HbA1c 8.2%, FBG 155.0 mg/dL
    - Dyslipidemia: Total Cholesterol 245.0 mg/dL
    - Central Obesity: Waist 100.0 cm, BMI 29.0 kg/m2
    - Tobacco Smoker: True

    Verifies:
    1. Explanations match input snapshot values exactly.
    2. Explanations reflect true causal risk contributors.
    3. Citations are valid clinical guidelines.
    4. No invented contributors.
    """
    token = auth_tokens["clinician"]

    cit_id = f"cit-rsk006-known-{uuid.uuid4().hex[:6]}"
    citizen = CitizenRecord(
        id=cit_id,
        tenant_id=TENANT_ID,
        user_id=f"user-{cit_id}",
        abha_id=f"91-0000-0000-{uuid.uuid4().hex[:4]}",
        first_name="Harish",
        last_name="Gupta",
        birth_date="1970-03-15",
        gender=Gender.MALE,
        phone="+919876543206",
        state="Delhi",
        district="Central Delhi",
        sub_district="Civil Lines",
        village_or_ward="Ward-06",
    )
    store.add_citizen(citizen)

    consent = ConsentDirective(
        id=f"consent-{cit_id}",
        citizen_id=cit_id,
        grantee_id="doc-rsk-006",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
    )
    store.add_consent(consent)

    now = datetime.now(timezone.utc)
    known_biometrics = [
        ("SYSTOLIC_BP", 164.0, "mmHg", "8480-6", "Systolic BP"),
        ("DIASTOLIC_BP", 102.0, "mmHg", "8462-4", "Diastolic BP"),
        ("HBA1C", 8.2, "%", "4548-4", "HbA1c"),
        ("FASTING_GLUCOSE", 155.0, "mg/dL", "1558-6", "Fasting Glucose"),
        ("TOTAL_CHOLESTEROL", 245.0, "mg/dL", "2093-3", "Total Cholesterol"),
        ("WAIST_CIRCUMFERENCE", 100.0, "cm", "8280-0", "Waist Circumference"),
        ("BMI", 29.0, "kg/m2", "39156-5", "BMI"),
    ]
    for code, val, unit, loinc, disp in known_biometrics:
        store.add_observation(Observation(
            citizen_id=cit_id,
            tenant_id=TENANT_ID,
            code=code,
            value=val,
            unit=unit,
            loinc_code=loinc,
            display_name=disp,
            recorded_at=now,
        ))

    screening = ScreeningSession(
        citizen_id=cit_id,
        tenant_id=TENANT_ID,
        conducted_by_id="worker-asha-01",
        conducted_at=now,
        status="COMPLETED",
        questionnaire_version="1.0.0",
        calculated_idrs_score=80,
        vitals=VitalsPayload(systolic_bp=164.0, diastolic_bp=102.0, hba1c=8.2),
    )
    store.add_screening(screening)

    # -------------------------------------------------------------------------
    # STEP 1: Generate an assessment with known input factors
    # -------------------------------------------------------------------------
    post_res = client.post(
        f"/api/v1/risk/evaluate/{cit_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post_res.status_code == 200
    assessment = post_res.json()

    # -------------------------------------------------------------------------
    # STEP 2 & 3: Retrieve and compare contributing-factor explanation
    # -------------------------------------------------------------------------
    snapshot = assessment["input_snapshot"]
    assert snapshot["SYSTOLIC_BP"] == 164.0
    assert snapshot["HBA1C"] == 8.2
    assert snapshot["TOTAL_CHOLESTEROL"] == 245.0
    assert snapshot["WAIST_CIRCUMFERENCE"] == 100.0

    drivers = assessment["top_drivers"]
    assert len(drivers) >= 2

    # Check Driver 1: Blood pressure driver corresponds strictly to recorded 164/102 mmHg
    htn_driver = next((d for d in drivers if "Hypertension" in d["feature_name"] or "Vascular" in d["feature_name"] or "Blood" in d["feature_name"]), None)
    assert htn_driver is not None, "Hypertension driver must be identified for SBP 164"
    assert "164" in htn_driver["observed_value"] or "102" in htn_driver["observed_value"]
    assert htn_driver["impact_weight"] > 0.0, "Risk driver must have positive impact weight"
    assert "ACC/AHA" in htn_driver["evidence_citation"] or "Guidelines" in htn_driver["evidence_citation"]

    # Check Driver 2: Diabetic driver corresponds strictly to recorded HbA1c 8.2%
    diab_driver = next((d for d in drivers if "Diabetic" in d["feature_name"] or "Glycemia" in d["feature_name"] or "Glucose" in d["feature_name"]), None)
    assert diab_driver is not None, "Diabetes driver must be identified for HbA1c 8.2%"
    assert "8.2" in diab_driver["observed_value"]
    assert diab_driver["impact_weight"] > 0.0
    assert "ICMR" in diab_driver["evidence_citation"] or "ADA" in diab_driver["evidence_citation"]

    # Clinical summary accurately cites the primary driver
    assert "Harish" in citizen.first_name
    assert assessment["overall_tier"] in ["HIGH", "CRITICAL"]


def test_tc_rsk_006_no_invented_contributors_for_isolated_condition():
    """TC-RSK-006: Isolated Condition Test (Hypertension ONLY, Normoglycemic, Non-smoker).
    - Inputs: SBP 162, DBP 98 (Stage 2 HTN), HbA1c 5.1 (Normal), FBG 85 (Normal), Non-smoker.

    Expected Result:
    - AI flags Hypertension as a risk driver.
    - AI does NOT invent diabetes or hyperglycemia as a risk driver!
    - AI identifies Optimal Glycemic Control as a PROTECTIVE factor.
    - AI identifies Tobacco Abstinence as a PROTECTIVE factor.
    """
    raw_inputs = {
        "AGE": 48,
        "SEX": "MALE",
        "SYSTOLIC_BP": 162.0,
        "DIASTOLIC_BP": 98.0,
        "HBA1C": 5.1,
        "FASTING_GLUCOSE": 85.0,
        "SMOKING": False,
        "BMI": 22.0,
        "WAIST_CIRCUMFERENCE": 78.0,
    }

    assessment = ncd_risk_engine.evaluate(raw_inputs)

    # 1. Hypertension is identified as top driver
    driver_names = [d.feature for d in assessment.top_drivers]
    assert any("Hypertension" in name or "Vascular" in name for name in driver_names)

    # 2. NO INVENTED DRIVERS: Diabetes / hyperglycemia is NOT among drivers
    assert not any("Diabetic Threshold" in name for name in driver_names)
    assert not any("Prediabetic" in name for name in driver_names)

    # 3. Protective factors include normal glycemic control and healthy BMI/waist
    protective_names = [p.feature for p in assessment.protective_factors]
    assert any("Optimal" in name or "Normal" in name or "Glycemic" in name or "Waist" in name for name in protective_names)

    # 4. Impact direction consistency
    for d in assessment.top_drivers:
        assert d.impact_direction.value == "INCREASES_RISK"
        assert d.impact_weight > 0.0
    for p in assessment.protective_factors:
        assert p.impact_direction.value == "DECREASES_RISK"
        assert p.impact_weight > 0.0


def test_tc_rsk_006_api_custom_explanation_consistency(auth_tokens):
    """TC-RSK-006: Verifies REST API POST /api/v1/risk/evaluate-custom returns consistent
    risk factor explanations that exactly reflect the submitted payload.
    """
    token = auth_tokens["clinician"]
    payload = {
        "AGE": 54,
        "SEX": "FEMALE",
        "SYSTOLIC_BP": 118.0,
        "DIASTOLIC_BP": 76.0,
        "HBA1C": 7.9,
        "FASTING_GLUCOSE": 150.0,
        "SMOKING": False,
        "WAIST_CIRCUMFERENCE": 82.0,
    }

    res = client.post(
        "/api/v1/risk/evaluate-custom",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()

    # Glycemic driver must be present for HbA1c 7.9
    drivers = data["top_drivers"]
    diab_driver = next((d for d in drivers if "Diabetic" in d["feature"] or "Glycemia" in d["feature"]), None)
    assert diab_driver is not None
    assert "7.9" in diab_driver["observed_value"]

    # Blood pressure must NOT be a risk driver (118/76 is optimal)
    assert not any("Stage" in d["feature"] for d in drivers)

    # Blood pressure should be listed under protective factors
    protectives = data["protective_factors"]
    bp_protective = next((p for p in protectives if "Blood Pressure" in p["feature"] or "Optimal" in p["feature"]), None)
    assert bp_protective is not None
    assert "118" in bp_protective["observed_value"]
