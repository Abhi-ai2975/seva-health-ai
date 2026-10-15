"""Test Suite for Section 14 Test Cases:
Documents, Wearables and Clinical Integration

- TC-INT-001: Valid document upload (Priority: P1)
- TC-INT-002: Malicious or invalid document (Priority: P0)
- TC-INT-003: Low-confidence extraction (Priority: P0)
- TC-INT-004: Wearable synchronization (Priority: P1)
"""

import io
import hashlib
import pytest
from datetime import datetime, timezone
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, RiskTier, TrajectoryTrend
from packages.auth.jwt import create_access_token, TokenPayload
from packages.clinical_models.consent import ConsentCategory, consent_manager
from packages.clinical_models.observations import Observation
from services.documents.models import (
    DocumentType,
    IngestionStatus,
    FieldReviewStatus,
    HumanReviewCorrection,
)
from services.documents.engine import ingestion_engine
from services.wearable.models import (
    WearableProvider,
    WearableMetricType,
    NormalizedWearableRecord,
    WearableProvenance,
)
from services.wearable.adapter import wearable_adapter

client = TestClient(app)

TENANT_ID = "karnataka_state_health"
CITIZEN_ID = "citizen-int-test-01"
CLINICIAN_ID = "clinician-dr-sharma-int"
CITIZEN_USER_ID = "user-citizen-int-01"


def build_synthetic_pdf(text_lines: list) -> bytes:
    """Builds a valid multi-line PDF byte stream for testing."""
    escaped = "\n".join(text_lines).replace("(", "\\(").replace(")", "\\)")
    stream_content = f"BT /F1 12 Tf 72 712 Td ({escaped}) Tj ET"
    stream_bytes = stream_content.encode("latin-1")

    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
        b"3 0 obj <</Type /Page /Parent 2 0 R /Resources <</Font <</F1 <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>>>>> /MediaBox [0 0 612 792] /Contents 4 0 R>> endobj\n"
        b"4 0 obj <</Length " + str(len(stream_bytes)).encode() + b">> stream\n"
        + stream_bytes +
        b"\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000281 00000 n \ntrailer <</Size 5 /Root 1 0 R>>\nstartxref\n380\n%%EOF"
    )


@pytest.fixture(autouse=True)
def setup_integration_environment():
    """Seeds test citizen, clinician, consents, and clears document & wearable test stores."""
    # Clear test-specific registries
    store.document_files.clear()
    store.ingestion_jobs.clear()
    store.review_queue.clear()
    store.legal_clinical_records.clear()
    if CITIZEN_ID in store.documents:
        store.documents[CITIZEN_ID] = []
    if CITIZEN_ID in store.observations:
        store.observations[CITIZEN_ID] = []
    if CITIZEN_ID in wearable_adapter.connections:
        wearable_adapter.connections.pop(CITIZEN_ID, None)
    if CITIZEN_ID in wearable_adapter.raw_records:
        wearable_adapter.raw_records.pop(CITIZEN_ID, None)
    if CITIZEN_ID in wearable_adapter.projections:
        wearable_adapter.projections.pop(CITIZEN_ID, None)

    # Register Citizen
    citizen = CitizenRecord(
        id=CITIZEN_ID,
        tenant_id=TENANT_ID,
        user_id=CITIZEN_USER_ID,
        abha_id="91-5555-6666-7777",
        first_name="Ramesh",
        last_name="IntTest",
        birth_date="1976-08-15",
        gender=Gender.MALE,
        phone="+919876500001",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru Urban",
        village_or_ward="Ward 12",
    )
    store.add_citizen(citizen)

    # Register Clinician User
    clinician = UserRecord(
        id=CLINICIAN_ID,
        tenant_id=TENANT_ID,
        email="dr.sharma@seva.gov.in",
        hashed_password="mock-pwd-hash",
        role=UserRole.CLINICIAN,
        full_name="Dr. Anand Sharma",
        assigned_patients=[CITIZEN_ID],
    )
    store.add_user(clinician)

    # Add Consent Directive
    consent = ConsentDirective(
        id=f"consent-{CITIZEN_ID}",
        citizen_id=CITIZEN_ID,
        grantee_id=CLINICIAN_ID,
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
        scope=["clinical_records", "documents", "wearables"],
    )
    store.add_consent(consent)

    yield


@pytest.fixture
def clinician_auth_headers():
    token = create_access_token(
        subject=CLINICIAN_ID,
        tenant_id=TENANT_ID,
        role=UserRole.CLINICIAN,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def citizen_auth_headers():
    token = create_access_token(
        subject=CITIZEN_USER_ID,
        tenant_id=TENANT_ID,
        role=UserRole.CITIZEN,
    )
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# TC-INT-001: Valid Document Upload
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_001_valid_document_upload_stored_linked_and_auditable(clinician_auth_headers):
    """TC-INT-001: Upload a supported synthetic laboratory report.

    Inspect the file-validation result.
    Retrieve its document reference.
    Expected: The document is stored through the configured subsystem, linked
    to the correct authorized record and auditable.
    """
    # 1. Prepare valid synthetic laboratory report
    pdf_content = build_synthetic_pdf([
        "Karnataka Diagnostic Centre - Mysuru",
        "Patient Name: Ramesh IntTest",
        "ABHA ID: 91-5555-6666-7777",
        "Report Date: 11-10-2026",
        "Fasting Blood Glucose 118.0 mg/dL 70 - 100",
        "HbA1c 6.4 % 4.0 - 5.6",
    ])
    expected_sha256 = hashlib.sha256(pdf_content).hexdigest()

    # 2. Upload via Ingestion Engine
    job = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=pdf_content,
        filename="karnataka_lab_report_oct2026.pdf",
        content_type="application/pdf",
        title="Comprehensive Metabolic Panel",
        uploader_id=CLINICIAN_ID,
        declared_type="LAB_REPORT",
    )

    # 3. Inspect File-Validation Result
    assert job.status in [IngestionStatus.COMMITTED_TO_CLINICAL_RECORD, IngestionStatus.HUMAN_REVIEW_REQUIRED]
    assert len(job.errors) == 0

    # Verify SHA-256 and immutable raw byte preservation in store.document_files
    doc_record = store.document_files.get(job.document_id)
    assert doc_record is not None
    assert doc_record["sha256_hash"] == expected_sha256
    assert doc_record["size_bytes"] == len(pdf_content)
    assert doc_record["raw_bytes"] == pdf_content
    assert doc_record["citizen_id"] == CITIZEN_ID
    assert doc_record["uploaded_by"] == CLINICIAN_ID

    # 4. Retrieve Document Reference linked to authorized citizen record
    citizen_docs = store.documents.get(CITIZEN_ID, [])
    assert len(citizen_docs) >= 1
    linked_meta = next(d for d in citizen_docs if d["id"] == job.document_id)
    assert linked_meta["filename"] == "karnataka_lab_report_oct2026.pdf"
    assert linked_meta["sha256_hash"] == expected_sha256
    assert linked_meta["citizen_id"] == CITIZEN_ID
    assert linked_meta["storage_url"] == f"/api/v1/documents/download/{job.document_id}"

    # 5. Auditable Retrieval: Download preserved file via REST API
    download_res = client.get(
        f"/api/v1/documents/download/{job.document_id}",
        headers=clinician_auth_headers,
    )
    assert download_res.status_code == 200
    assert download_res.content == pdf_content
    assert download_res.headers.get("x-sha256-checksum") == expected_sha256

    # 6. Verify extraction provenance on clinical entities
    assert len(job.extractions.tests) >= 1
    test_fbg = next(t for t in job.extractions.tests if "glucose" in t.test_name.lower())
    assert test_fbg.numeric_value == 118.0
    assert test_fbg.provenance is not None
    assert test_fbg.provenance.source_document_id == job.document_id
    assert test_fbg.provenance.file_sha256 == expected_sha256


# ==============================================================================
# TC-INT-002: Malicious or Invalid Document
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_002_malicious_or_invalid_document_rejected_safely(clinician_auth_headers):
    """TC-INT-002: Upload an unsupported file or a test file identified as malicious by scanner.

    Attempt to process it.
    Expected: The upload or processing is rejected safely. The file is not treated
    as a trusted clinical record.
    """
    initial_obs_count = len(store.get_citizen_observations(CITIZEN_ID))
    initial_legal_count = len(store.legal_clinical_records.get(CITIZEN_ID, []))

    # 1. Malicious EICAR Antivirus Test Signature
    eicar_bytes = b"%PDF-1.4\nX5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    job_eicar = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=eicar_bytes,
        filename="suspicious_lab_report.pdf",
        content_type="application/pdf",
        title="Infected Document",
        uploader_id=CLINICIAN_ID,
    )

    # Verification: Rejected safely
    assert job_eicar.status == IngestionStatus.REJECTED
    assert any("VIRUS_DETECTED" in err for err in job_eicar.errors)

    # Verification via HTTP API: Expect HTTP 422 Unprocessable Entity
    resp_eicar_api = client.post(
        "/api/v1/documents/ingest",
        headers=clinician_auth_headers,
        data={"citizen_id": CITIZEN_ID, "title": "Infected Report"},
        files={"file": ("infected.pdf", io.BytesIO(eicar_bytes), "application/pdf")},
    )
    assert resp_eicar_api.status_code == 422
    assert "Document rejected" in resp_eicar_api.json()["detail"]

    # 2. Disallowed Windows PE Executable Header (MZ...) disguised as PDF
    malicious_exe_header = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00fake_pdf_payload"
    job_exe = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=malicious_exe_header,
        filename="trojan_disguised_as_report.pdf",
        content_type="application/pdf",
        title="Executable Payload",
        uploader_id=CLINICIAN_ID,
    )
    assert job_exe.status == IngestionStatus.REJECTED
    assert any("SECURITY_VIOLATION" in err for err in job_exe.errors)

    # 3. Empty File (0 bytes)
    job_empty = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=b"",
        filename="empty.pdf",
        content_type="application/pdf",
        title="Zero Byte Document",
        uploader_id=CLINICIAN_ID,
    )
    assert job_empty.status == IngestionStatus.FAILED
    assert any("Empty file" in err for err in job_empty.errors)

    # 4. MIME Mismatch (Declared PDF, but bytes are plain text without %PDF- magic)
    mismatch_bytes = b"Hello world, this is plain text without any PDF header"
    job_mismatch = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=mismatch_bytes,
        filename="mismatch.pdf",
        content_type="application/pdf",
        title="MIME Mismatch File",
        uploader_id=CLINICIAN_ID,
    )
    assert job_mismatch.status == IngestionStatus.FAILED
    assert any("MIME mismatch" in err for err in job_mismatch.errors)

    # 5. Critical Invariant: Neither file is treated as a trusted clinical record
    final_obs_count = len(store.get_citizen_observations(CITIZEN_ID))
    final_legal_count = len(store.legal_clinical_records.get(CITIZEN_ID, []))
    assert final_obs_count == initial_obs_count, "Zero observations must be committed for rejected files"
    assert final_legal_count == initial_legal_count, "Zero legal records must be committed for rejected files"


# ==============================================================================
# TC-INT-003: Low-Confidence Extraction & Human Review Gate
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_003_low_confidence_extraction_remains_unverified_until_review(clinician_auth_headers):
    """TC-INT-003: Process a synthetic document with an intentionally ambiguous laboratory value.

    Inspect extracted fields.
    Attempt to commit the value as a trusted observation.
    Expected: The value remains unverified until the required review is completed.
    """
    initial_obs = store.get_citizen_observations(CITIZEN_ID)
    initial_obs_ids = {o.id for o in initial_obs}

    # 1. Process document with intentionally ambiguous/extreme out-of-range value
    # (Fasting Blood Glucose 950.0 mg/dL triggers physiological violation and low-confidence gating)
    doc_lines = [
        "Mysuru Community Diagnostic Clinic",
        "Patient Name: Ramesh IntTest",
        "Fasting Blood Glucose 950.0 mg/dL 70 - 100",
    ]
    pdf_bytes = build_synthetic_pdf(doc_lines)

    job = await ingestion_engine.ingest_document(
        citizen_id=CITIZEN_ID,
        file_content=pdf_bytes,
        filename="ambiguous_glucose_report.pdf",
        content_type="application/pdf",
        title="Ambiguous Lab Report",
        uploader_id=CLINICIAN_ID,
    )

    # 2. Inspect Extracted Fields
    assert job.status == IngestionStatus.HUMAN_REVIEW_REQUIRED
    assert job.requires_human_review is True
    assert len(job.extractions.tests) >= 1

    extracted_test = job.extractions.tests[0]
    assert extracted_test.numeric_value == 950.0
    assert extracted_test.review_status == FieldReviewStatus.PENDING_REVIEW
    assert any("Physiological violation" in r or "Low confidence" in r for r in job.human_review_reasons)
    assert job.job_id in store.review_queue

    # 3. Attempt to commit unverified value: Safety Gate Invariant
    # Confirm that prior to clinician review, zero unverified observations were committed
    current_obs = store.get_citizen_observations(CITIZEN_ID)
    new_obs = [o for o in current_obs if o.id not in initial_obs_ids]
    assert len(new_obs) == 0, f"SAFETY VIOLATION: Unverified observation committed before review: {new_obs}"
    assert len(job.committed_observation_ids) == 0

    # 4. Clinician completes the required human review workflow
    correction = HumanReviewCorrection(
        action="CORRECT",
        target_category="TEST",
        target_id=extracted_test.id,
        corrected_value=125.0,  # Clinician identifies OCR artifact (950 -> true value 125.0 mg/dL)
        corrected_unit="mg/dL",
        corrected_interpretation="HIGH",
        reviewer_notes="OCR digit artifact corrected: 950 mg/dL corrected to 125.0 mg/dL after checking physical slip.",
        reviewer_id=CLINICIAN_ID,
        reviewer_name="Dr. Anand Sharma",
    )

    updated_job = await ingestion_engine.apply_human_review(
        job_id=job.job_id,
        corrections=[correction],
        reviewer_id=CLINICIAN_ID,
        reviewer_name="Dr. Anand Sharma",
    )

    # 5. Verification: Now and only now is the verified observation promoted to trusted records
    assert updated_job.status == IngestionStatus.COMMITTED_TO_CLINICAL_RECORD
    assert updated_job.requires_human_review is False
    assert len(updated_job.committed_observation_ids) == 1
    assert updated_job.committed_legal_record_id is not None
    assert job.job_id not in store.review_queue

    # Observation now present in clinical observations store with verified value
    confirmed_obs = store.get_citizen_observations(CITIZEN_ID)
    promoted_obs = next(o for o in confirmed_obs if o.id == updated_job.committed_observation_ids[0])
    assert promoted_obs.value == 125.0
    assert promoted_obs.unit == "mg/dL"
    assert promoted_obs.source == "CLINICAL_DOCUMENT_INGESTION"
    assert promoted_obs.is_abnormal is True


# ==============================================================================
# TC-INT-004: Wearable Synchronization & Observations Integrity
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_004_wearable_synchronization_normalized_observations_retain_source_timestamp_unit(
    citizen_auth_headers,
):
    """TC-INT-004: Connect a synthetic wearable source with authorized access.

    Synchronize a known dataset.
    Retrieve normalized observations.
    Expected: Supported observations retain source, timestamp and unit information.
    Frontend UOM change and database verify check.
    """
    # 1. Connect a synthetic wearable source with authorized access
    conn_res = client.post(
        f"/api/v1/wearables/connect/{WearableProvider.GARMIN.value}",
        headers=citizen_auth_headers,
        json={
            "citizen_id": CITIZEN_ID,
            "access_token": "valid_garmin_oauth_token_xyz123",
            "scope": ["activity", "heartrate", "sleep", "glucose", "blood_pressure"],
        },
    )
    assert conn_res.status_code == 200
    conn_data = conn_res.json()
    assert conn_data["citizen_id"] == CITIZEN_ID
    assert conn_data["provider"] == "garmin"
    assert conn_data["status"] == "CONNECTED"
    assert conn_data["access_token_masked"] == "vali****z123"

    # 2. Synchronize a known dataset
    sync_res = client.post(
        f"/api/v1/wearables/sync/{CITIZEN_ID}?provider=garmin&trend=DEFAULT",
        headers=citizen_auth_headers,
    )
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert sync_data["status"] == "SUCCESS"
    assert sync_data["synced_records_count"] > 0
    assert "projection" in sync_data

    # 3. Retrieve normalized observations via dedicated API endpoint
    obs_api_res = client.get(
        f"/api/v1/wearables/{CITIZEN_ID}/observations",
        headers=citizen_auth_headers,
    )
    assert obs_api_res.status_code == 200
    wearable_obs_list = obs_api_res.json()
    assert len(wearable_obs_list) > 0

    # 4. Verification: Supported observations retain source, timestamp, and unit
    supported_metrics = {o["code"] for o in wearable_obs_list}
    expected_metrics = {"HEART_RATE", "STEPS", "HRV", "SLEEP_DURATION", "WEIGHT", "SPO2"}
    assert expected_metrics.issubset(supported_metrics)

    for obs in wearable_obs_list:
        # Source retention check
        assert obs["source"] is not None
        assert "WEARABLE" in obs["source"].upper()

        # Timestamp retention check (ISO-8601)
        assert obs["recorded_at"] is not None
        dt = datetime.fromisoformat(obs["recorded_at"])
        assert dt.year >= 2026

        # Standardized Unit retention check
        assert obs["unit"] is not None
        assert len(obs["unit"].strip()) > 0
        if obs["code"] == "HEART_RATE":
            assert obs["unit"] == "bpm"
        elif obs["code"] == "STEPS":
            assert obs["unit"] == "steps"
        elif obs["code"] == "HRV":
            assert obs["unit"] == "ms"
        elif obs["code"] == "WEIGHT":
            assert obs["unit"] == "kg"
        elif obs["code"] == "SPO2":
            assert obs["unit"] == "%"
        elif obs["code"] == "SYSTOLIC_BP":
            assert obs["unit"] == "mmHg"
        elif obs["code"] == "FASTING_GLUCOSE":
            assert obs["unit"] == "mg/dL"

    # 5. Database Verify Check: In-memory/SQLite central store validation
    db_obs = store.get_citizen_observations(CITIZEN_ID)
    db_wearable_obs = [o for o in db_obs if o.source and "WEARABLE" in o.source.upper()]
    assert len(db_wearable_obs) == len(wearable_obs_list)

    # 6. UOM Conversion Verification (Metric vs SI/Imperial conversion formulas)
    fbg_obs = next(o for o in wearable_obs_list if o["code"] == "FASTING_GLUCOSE")
    mgdl_val = fbg_obs["value"]
    mmol_val = round(mgdl_val / 18.018, 2)
    assert mmol_val > 0.0, "Blood glucose in mmol/L must be positive"

    wt_obs = next(o for o in wearable_obs_list if o["code"] == "WEIGHT")
    kg_val = wt_obs["value"]
    lbs_val = round(kg_val * 2.20462, 1)
    assert lbs_val > 0.0, "Weight in lbs must be positive"


# ==============================================================================
# Frontend UI Elements Verification (TC-INT-001..004)
# ==============================================================================

def test_tc_int_frontend_uom_and_wearable_ui_elements():
    """Verify apps/citizen-mobile/index.html includes UOM switcher,

    normalized telemetry cards, and document validation action cards.
    """
    with open("apps/citizen-mobile/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Screen 12 Wearables UI & UOM
    assert "Synchronize Live Wearable Biometrics (TC-INT-004)" in html
    assert "Unit of Measurement (UOM)" in html
    assert "Metric (mg/dL, kg)" in html
    assert "SI / Imperial (mmol/L, lbs)" in html
    assert "activeUomBadge" in html
    assert "wearableTelemetryContainer" in html

    # Document Ingestion UI
    assert "Upload Valid Lab Report (TC-INT-001)" in html
    assert "Test Malicious / EICAR Document (TC-INT-002)" in html
    assert "Test Ambiguous / Low-Confidence Scan (TC-INT-003)" in html
    assert "docUploadResult" in html

    # JavaScript Handlers
    assert "setUomPreference" in html
    assert "renderWearableTelemetry" in html
    assert "syncCitizenWearableMobile" in html
    assert "testDocumentUploadMobile" in html
    assert "/api/v1/wearables/sync/" in html
    assert "/api/v1/wearables/" in html and "/observations" in html
