"""Test Suite for TC-RSK-003: Demonstration-only model labelling (Priority: P0).

Steps:
1. Run an assessment using a demonstration-only model.
2. View the result in the API and UI.
3. Inspect exported reports.

Expected result:
The result is visibly and consistently labelled as demonstration-only and not clinically validated.
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, RiskTier
from packages.clinical_models.observations import Observation
from packages.auth.jwt import create_access_token

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"
CITIZEN_ID = "citizen-rsk-003-demo"


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


@pytest.fixture(autouse=True)
def setup_citizen_fixture():
    """Ensure a synthetic citizen with observations exists for TC-RSK-003."""
    c = CitizenRecord(
        id=CITIZEN_ID,
        tenant_id=TENANT_ID,
        user_id="user-rsk-003",
        abha_id="91-9999-8888-7777",
        first_name="Ramesh",
        last_name="DemonstrationPatel",
        birth_date="1978-05-15",
        gender=Gender.MALE,
        phone="+919876543210",
        state="Karnataka",
        district="Bengaluru Urban",
        sub_district="Bengaluru South",
        village_or_ward="Ward 150",
    )
    store.citizens[CITIZEN_ID] = c

    # Set up clinician user and patient assignment / consent
    clinician = UserRecord(
        id="clinician-dr-sharma",
        tenant_id=TENANT_ID,
        email="dr.sharma@health.karnataka.gov.in",
        hashed_password="mock_hashed_password",
        role=UserRole.CLINICIAN,
        full_name="Dr. Sharma",
        assigned_patients=[CITIZEN_ID],
    )
    store.add_user(clinician)

    consent = ConsentDirective(
        id="consent-rsk-003",
        citizen_id=CITIZEN_ID,
        grantee_id="clinician-dr-sharma",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
        scope=["OBSERVATIONS", "RISK_ASSESSMENT", "SCREENING"],
    )
    store.add_consent(consent)

    # Add standard biometric observations
    now = datetime.now(timezone.utc)
    obs = [
        Observation(
            id="obs-rsk-sbp",
            tenant_id=TENANT_ID,
            citizen_id=CITIZEN_ID,
            code="SYSTOLIC_BP",
            loinc_code="8480-6",
            display_name="Systolic Blood Pressure",
            value=142.0,
            unit="mmHg",
            recorded_at=now,
        ),
        Observation(
            id="obs-rsk-fbg",
            tenant_id=TENANT_ID,
            citizen_id=CITIZEN_ID,
            code="FASTING_GLUCOSE",
            loinc_code="1558-6",
            display_name="Fasting Blood Glucose",
            value=135.0,
            unit="mg/dL",
            recorded_at=now,
        ),
        Observation(
            id="obs-rsk-bmi",
            tenant_id=TENANT_ID,
            citizen_id=CITIZEN_ID,
            code="BMI",
            loinc_code="39156-5",
            display_name="Body Mass Index",
            value=26.4,
            unit="kg/m2",
            recorded_at=now,
        ),
    ]
    store.observations[CITIZEN_ID] = obs


# ==============================================================================
# TEST 1: Model Registry Catalog Verification
# ==============================================================================

def test_demonstration_model_registered_in_catalog(auth_tokens):
    """Verify that demonstration models are explicitly registered and identified as unvalidated."""
    resp = client.get(
        "/api/v1/risk/models",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert resp.status_code == 200
    models = resp.json()
    demo_models = [m for m in models if m.get("key") == "demonstration_proxy"]
    assert len(demo_models) == 1, "Demonstration proxy model must be registered in catalog"
    demo_model = demo_models[0]

    assert demo_model["is_clinically_validated"] is False
    assert demo_model["provenance"]["validation_status"] == "DEMONSTRATION_ONLY"
    assert any("DEMONSTRATION ONLY" in lim for lim in demo_model["limitations"])


# ==============================================================================
# TEST 2: Run Assessment Using Demonstration-Only Model (Step 1 & 2)
# ==============================================================================

def test_run_assessment_using_demonstration_model_api(auth_tokens):
    """Step 1 & 2: Run an assessment using a demonstration-only model and verify API response."""
    resp = client.post(
        f"/api/v1/risk/evaluate-demonstration/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Core TC-RSK-003 Assertions: Visibly and consistently labelled as demonstration-only and not clinically validated
    assert data["is_demonstration_only"] is True, "Must be flagged as demonstration only"
    assert data["is_clinically_validated"] is False, "Must NOT be flagged as clinically validated"
    assert data["validation_status"] == "DEMONSTRATION_ONLY"
    assert "DEMONSTRATION ONLY" in data["demonstration_warning"].upper()
    assert "NOT CLINICALLY VALIDATED" in data["demonstration_warning"].upper()
    assert "[DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED]" in data["clinical_summary"]
    assert "DEMONSTRATION ONLY" in data["safety_disclaimer"]
    assert any("DEMONSTRATION ONLY" in lim for lim in data["limitations"])


def test_evaluate_query_param_demonstration_mode(auth_tokens):
    """Verify POST /evaluate/{citizen_id}?demonstration_model=true delegates to demonstration labelling."""
    resp = client.post(
        f"/api/v1/risk/evaluate/{CITIZEN_ID}?demonstration_model=true",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_demonstration_only"] is True
    assert data["is_clinically_validated"] is False
    assert data["validation_status"] == "DEMONSTRATION_ONLY"
    assert data["demonstration_warning"] is not None


def test_evaluate_modular_demonstration_mode(auth_tokens):
    """Verify modular evaluation endpoint properly tags demonstration proxy results."""
    resp = client.post(
        f"/api/v1/risk/evaluate-modular/{CITIZEN_ID}?demonstration_model=true",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_demonstration_only"] is True
    assert data["is_clinically_validated"] is False
    assert data["validation_status"] == "DEMONSTRATION_ONLY"
    assert "DEMONSTRATION ONLY" in data["clinical_safety_notice"]
    assert "DEMONSTRATION ONLY" in data["demonstration_warning"]


# ==============================================================================
# TEST 3: Inspect Exported Reports (Step 3: FHIR R4 & Clinical Export Reports)
# ==============================================================================

def test_inspect_exported_reports_fhir_r4(auth_tokens):
    """Step 3: Inspect exported FHIR R4 RiskAssessment resource for demonstration tags and extensions."""
    # Run demonstration assessment
    eval_resp = client.post(
        f"/api/v1/risk/evaluate-demonstration/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert eval_resp.status_code == 200

    # Retrieve FHIR R4 RiskAssessment
    fhir_resp = client.get(
        f"/api/v1/fhir/RiskAssessment/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert fhir_resp.status_code == 200, fhir_resp.text
    fhir_data = fhir_resp.json()

    # Verify resource structure
    assert fhir_data["resourceType"] == "RiskAssessment"

    # Verify meta.tag labelling
    tags = fhir_data.get("meta", {}).get("tag", [])
    demo_tags = [t for t in tags if t.get("code") == "DEMONSTRATION_ONLY"]
    assert len(demo_tags) == 1, "FHIR resource must include DEMONSTRATION_ONLY meta tag"
    assert "NOT CLINICALLY VALIDATED" in demo_tags[0]["display"]

    # Verify extension labelling
    extensions = fhir_data.get("extension", [])
    demo_ext = [e for e in extensions if "demonstration-only" in e.get("url", "")]
    assert len(demo_ext) >= 1
    assert demo_ext[0]["valueBoolean"] is True

    # Verify note disclaimer
    notes = [n.get("text", "") for n in fhir_data.get("note", [])]
    assert any("DEMONSTRATION ONLY" in text.upper() and "NOT CLINICALLY VALIDATED" in text.upper() for text in notes)


def test_inspect_exported_reports_clinical_export_bundle(auth_tokens):
    """Step 3: Inspect exported clinical report bundle for visible watermarks, headers, and disclaimers."""
    # Run demonstration assessment
    eval_resp = client.post(
        f"/api/v1/risk/evaluate-demonstration/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert eval_resp.status_code == 200
    assessment_id = eval_resp.json()["id"]

    # Retrieve clinical export report
    report_resp = client.get(
        f"/api/v1/risk/export-report/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert report_resp.status_code == 200, report_resp.text
    report = report_resp.json()

    # Core report fields
    assert report["is_demonstration_only"] is True
    assert report["is_clinically_validated"] is False
    assert report["validation_status"] == "DEMONSTRATION_ONLY"
    assert "DEMONSTRATION ONLY" in report["demonstration_warning"]

    # Watermark and Header Banner
    assert report["watermark"] == "DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED"
    assert "DEMONSTRATION ONLY" in report["header_banner"]
    assert "NOT CLINICALLY VALIDATED" in report["header_banner"]

    # Markdown Content Inspection
    md = report["markdown_content"]
    assert "⚠️ DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED FOR MEDICAL DECISIONS" in md
    assert "DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED" in md
    assert "Validation Status:** **DEMONSTRATION_ONLY**" in md
    assert "Clinically Validated:** NO (DEMONSTRATION PROXY)" in md

    # HTML Content Inspection (Persistent watermark overlay and banner)
    html = report["html_content"]
    assert "DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED" in html
    assert "VALIDATION STATUS: DEMONSTRATION_ONLY" in html
    assert "NO (DEMONSTRATION PROXY)" in html

    # Export by Assessment ID
    id_report_resp = client.get(
        f"/api/v1/risk/assessment/{assessment_id}/export-report",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert id_report_resp.status_code == 200
    assert id_report_resp.json()["watermark"] == "DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED"


# ==============================================================================
# TEST 4: Clinical Discrimination Test (Validated vs Demonstration Models)
# ==============================================================================

def test_validated_model_does_not_have_demonstration_warning(auth_tokens):
    """Ensure standard clinically validated model runs do not falsely trigger demonstration warnings."""
    resp = client.post(
        f"/api/v1/risk/evaluate/{CITIZEN_ID}?demonstration_model=false",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_demonstration_only"] is False
    assert data["is_clinically_validated"] is True
    assert data["validation_status"] == "CLINICALLY_VALIDATED"
    assert data["demonstration_warning"] is None
    assert "[DEMONSTRATION ONLY" not in data["clinical_summary"]

    # FHIR Export check for validated model
    fhir_resp = client.get(
        f"/api/v1/fhir/RiskAssessment/{CITIZEN_ID}",
        headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
    )
    assert fhir_resp.status_code == 200
    fhir_data = fhir_resp.json()
    tags = fhir_data.get("meta", {}).get("tag", [])
    assert any(t.get("code") == "CLINICALLY_VALIDATED" for t in tags)
    assert not any(t.get("code") == "DEMONSTRATION_ONLY" for t in tags)
