"""TC-RSK-004: Model-version preservation
Priority: P0
Requirement: SYS-RSK-001 through SYS-RSK-006

Steps:
1. Generate an assessment using Model Version A.
2. Deploy a test version of Model Version B.
3. Retrieve the historical assessment.
4. Generate a new assessment.

Expected result:
The historical assessment retains Version A, while the new assessment records Version B.
Historical assessment remains immutable and is not overwritten or mutated.
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
from services.risk_engine.base import RiskModel, DomainRiskResult, RiskCategory

client = TestClient(app)

TENANT_ID = "tenant-delhi-01"


@pytest.fixture
def auth_tokens():
    """Generates valid JWT tokens for required personas."""
    return {
        "clinician": create_access_token(
            subject="doc-rsk-004",
            tenant_id=TENANT_ID,
            role=UserRole.CLINICIAN,
            scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
        ),
    }


def test_tc_rsk_004_model_version_preservation_composite_engine(auth_tokens):
    """TC-RSK-004 Step 1-4 Verification on Composite/Modular Engine:
    1. Generate assessment with Version A (calibrated_ensemble_v1.0.0).
    2. Deploy test version Version B (calibrated_ensemble_v2.0.0-rc1).
    3. Retrieve historical assessment: retains Version A.
    4. Generate new assessment: records Version B.
    """
    token = auth_tokens["clinician"]

    # Precondition: Set initial model version to Version A
    version_a = "calibrated_ensemble_v1.0.0"
    version_b = "calibrated_ensemble_v2.0.0-rc1"
    ncd_risk_engine.set_engine_version(version_a)

    # Register synthetic citizen
    cit_id = f"cit-rsk004-{uuid.uuid4().hex[:6]}"
    citizen = CitizenRecord(
        id=cit_id,
        tenant_id=TENANT_ID,
        user_id=f"user-{cit_id}",
        abha_id=f"91-0000-0000-{uuid.uuid4().hex[:4]}",
        first_name="Rajendra",
        last_name="Kumar",
        birth_date="1975-08-20",
        gender=Gender.MALE,
        phone="+919876543201",
        state="Delhi",
        district="Central Delhi",
        sub_district="Civil Lines",
        village_or_ward="Ward-04",
    )
    store.add_citizen(citizen)

    consent = ConsentDirective(
        id=f"consent-{cit_id}",
        citizen_id=cit_id,
        grantee_id="doc-rsk-004",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
    )
    store.add_consent(consent)

    now = datetime.now(timezone.utc)
    obs = Observation(
        citizen_id=cit_id,
        tenant_id=TENANT_ID,
        code="SYSTOLIC_BP",
        value=155.0,
        unit="mmHg",
        loinc_code="8480-6",
        display_name="Systolic BP",
        recorded_at=now,
    )
    store.add_observation(obs)

    screening = ScreeningSession(
        citizen_id=cit_id,
        tenant_id=TENANT_ID,
        conducted_by_id="worker-asha-01",
        conducted_at=now,
        status="COMPLETED",
        questionnaire_version="1.0.0",
        calculated_idrs_score=60,
        vitals=VitalsPayload(systolic_bp=155.0, diastolic_bp=95.0),
    )
    store.add_screening(screening)

    # -------------------------------------------------------------------------
    # STEP 1: Generate an assessment using Model Version A
    # -------------------------------------------------------------------------
    res_a = client.post(
        f"/api/v1/risk/evaluate/{cit_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_a.status_code == 200, res_a.text
    assessment_a_data = res_a.json()
    assessment_a_id = assessment_a_data["id"]

    assert assessment_a_data["model_version"] == version_a
    assert assessment_a_data["overall_score"] is not None

    # -------------------------------------------------------------------------
    # STEP 2: Deploy a test version of Model Version B
    # -------------------------------------------------------------------------
    ncd_risk_engine.set_engine_version(version_b)

    # -------------------------------------------------------------------------
    # STEP 3: Retrieve the historical assessment
    # -------------------------------------------------------------------------
    # Retrieve via GET /api/v1/risk/assessment/{assessment_id}
    res_hist = client.get(
        f"/api/v1/risk/assessment/{assessment_a_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_hist.status_code == 200, res_hist.text
    hist_data = res_hist.json()

    # Expected Result: Historical assessment retains Version A!
    assert hist_data["id"] == assessment_a_id
    assert hist_data["model_version"] == version_a, (
        f"Historical assessment must retain Version A '{version_a}', but got '{hist_data['model_version']}'"
    )

    # Also verify direct store retrieval
    stored_hist = store.get_risk_assessment(assessment_a_id)
    assert stored_hist is not None
    assert stored_hist.model_version == version_a

    # -------------------------------------------------------------------------
    # STEP 4: Generate a new assessment
    # -------------------------------------------------------------------------
    res_b = client.post(
        f"/api/v1/risk/evaluate/{cit_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_b.status_code == 200, res_b.text
    assessment_b_data = res_b.json()
    assessment_b_id = assessment_b_data["id"]

    # Expected Result: The new assessment records Version B!
    assert assessment_b_id != assessment_a_id
    assert assessment_b_data["model_version"] == version_b, (
        f"New assessment must record Version B '{version_b}', but got '{assessment_b_data['model_version']}'"
    )

    # Verify chronological history preserves both Version A and Version B
    res_history = client.get(
        f"/api/v1/risk/history/{cit_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_history.status_code == 200
    all_assessments = res_history.json()
    assert len(all_assessments) >= 2

    versions = [a["model_version"] for a in all_assessments]
    assert version_a in versions, "History must contain Version A assessment"
    assert version_b in versions, "History must contain Version B assessment"

    # Teardown: Reset engine version back to standard release
    ncd_risk_engine.set_engine_version("calibrated_ensemble_v1.0.0")


def test_tc_rsk_004_domain_model_version_preservation():
    """TC-RSK-004 Domain Model Level:
    Verifies that deploying a new version of a domain model preserves earlier calculations.
    """
    class MockHypertensionModelV2(RiskModel):
        @property
        def domain(self) -> str:
            return "Hypertension & Vascular Risk"

        def version(self) -> str:
            return "ACC-AHA-2017-ICMR.v2.0-experimental"

        def provenance(self):
            return {"guideline": "Experimental Guideline"}

        def is_clinically_validated(self):
            return False

        def required_inputs(self):
            return ["SYSTOLIC_BP", "DIASTOLIC_BP"]

        def optional_inputs(self):
            return []

        def limitations(self):
            return ["Experimental version"]

        def confidence(self, inputs):
            return 1.0

        def calculate(self, inputs):
            clean, _, _ = self.validate_and_normalize_inputs(inputs)
            return DomainRiskResult(
                risk_domain=self.domain,
                score=0.88,
                risk_category=RiskCategory.HIGH,
                model_version=self.version(),
                input_snapshot=clean,
                confidence=1.0,
                limitations=self.limitations(),
                recommended_next_step="Consult physician",
                is_clinically_validated=False,
            )

        def explain(self, inputs, result):
            return []

        def recommended_next_step(self, cat, contribs):
            return "Step"

    # Step 1: Calculate with Version A
    original_model = ncd_risk_engine.get_model("hypertension")
    assert original_model is not None
    version_a = original_model.version()

    res_a = ncd_risk_engine.evaluate_model("hypertension", {"SYSTOLIC_BP": 150.0, "DIASTOLIC_BP": 95.0})
    assert res_a.model_version == version_a

    # Step 2: Deploy Version B
    v2_model = MockHypertensionModelV2()
    ncd_risk_engine.deploy_model("hypertension", v2_model)

    # Step 3: Historical result object remains immutable Version A
    assert res_a.model_version == version_a

    # Step 4: New assessment reflects Version B
    res_b = ncd_risk_engine.evaluate_model("hypertension", {"SYSTOLIC_BP": 150.0, "DIASTOLIC_BP": 95.0})
    assert res_b.model_version == "ACC-AHA-2017-ICMR.v2.0-experimental"

    # Teardown: Restore original model
    ncd_risk_engine.deploy_model("hypertension", original_model)
