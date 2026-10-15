"""Test Suite for TC-SEC-003: File Path Traversal Defense & Storage Confinement (Priority: P0).

Verifies:
1. Detection of traversal-style file names and paths (relative, encoded, null-byte, absolute).
2. Strict storage confinement preventing escape from the authorized vault root.
3. Ingestion sanitization ensuring uploaded files are confined to authorized storage.
4. Retrieval rejection of unauthorized paths and traversal attempts.
5. Security audit logging of all path traversal incidents.
"""

import pytest
import io
from pathlib import Path
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store
from packages.security.path_traversal_guard import (
    PathTraversalDetector,
    StorageVaultManager,
    PathTraversalViolationError,
)
from packages.auth.jwt import create_access_token
from packages.types.enums import UserRole


from services.store import UserRecord, CitizenRecord
from packages.types.enums import Gender


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_traversal_test_context():
    """Ensure baseline test clinician and citizens exist."""
    clinician_user = UserRecord(
        id="user-traversal-doc",
        tenant_id="district-mandya",
        email="traversal_doc@seva.gov.in",
        hashed_password="hashed_pwd",
        role=UserRole.CLINICIAN,
        full_name="Dr. File Security Tester",
    )
    store.add_user(clinician_user)

    cit_1 = CitizenRecord(
        id="cit_001",
        tenant_id="district-mandya",
        user_id="user-cit-001",
        abha_id="91-1111-2222-3333",
        first_name="Ramesh",
        last_name="Kumar",
        birth_date="1980-01-01",
        gender=Gender.MALE,
        phone="+919988776601",
        state="Karnataka",
        district="Mandya",
        sub_district="Mandya Rural",
        village_or_ward="Ward 1",
    )
    store.add_citizen(cit_1)

    cit_2 = CitizenRecord(
        id="cit_002",
        tenant_id="district-mandya",
        user_id="user-cit-002",
        abha_id="91-4444-5555-6666",
        first_name="Priya",
        last_name="Nair",
        birth_date="1990-02-02",
        gender=Gender.FEMALE,
        phone="+919988776602",
        state="Karnataka",
        district="Mandya",
        sub_district="Mandya Rural",
        village_or_ward="Ward 2",
    )
    store.add_citizen(cit_2)


@pytest.fixture
def clinician_headers():
    token = create_access_token(
        subject="user-traversal-doc",
        tenant_id="district-mandya",
        role=UserRole.CLINICIAN,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def citizen_headers():
    token = create_access_token(
        subject="cit_001",
        tenant_id="district-mandya",
        role=UserRole.CITIZEN,
    )
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Detection Engine Unit Tests
# ==============================================================================

class TestTCSEC003DetectionEngine:
    """Validates PathTraversalDetector against representative traversal vectors."""

    @pytest.mark.parametrize(
        "payload, expected_cat",
        [
            ("../../etc/passwd", "RELATIVE_TRAVERSAL"),
            ("..\\..\\windows\\win.ini", "RELATIVE_TRAVERSAL"),
            ("....//....//etc/shadow", "RELATIVE_TRAVERSAL"),
            ("report/../../secret.pdf", "RELATIVE_TRAVERSAL"),
            ("%2e%2e%2f%2e%2e%2fetc%2fpasswd", "URL_ENCODED_TRAVERSAL"),
            ("%252e%252e%252fetc%252fhosts", "URL_ENCODED_TRAVERSAL"),
            ("report.pdf\x00.exe", "NULL_BYTE_INJECTION"),
            ("lab_report.pdf%00/etc/passwd", "NULL_BYTE_INJECTION"),
            ("/etc/passwd", "ABSOLUTE_SYSTEM_PATH"),
            ("/var/log/syslog", "ABSOLUTE_SYSTEM_PATH"),
            ("C:\\Windows\\System32\\drivers\\etc\\hosts", "ABSOLUTE_SYSTEM_PATH"),
            ("\\\\malicious-server\\share\\file.pdf", "WINDOWS_UNC_PATH"),
            ("CON.pdf", "SPECIAL_DEVICE_NAME"),
        ],
    )
    def test_detector_identifies_traversal_vectors(self, payload: str, expected_cat: str):
        is_malicious, cat, desc = PathTraversalDetector.inspect(payload)
        assert is_malicious is True, f"Failed to detect traversal in: {payload}"
        assert cat is not None
        assert desc is not None

    def test_detector_passes_benign_filenames(self):
        clean_filenames = [
            "lab_report_2026.pdf",
            "cbc_panel_march.png",
            "prescription_dr_sharma.pdf",
            "lipid_profile_v1.jpg",
            "blood_glucose_log.pdf",
        ]
        for name in clean_filenames:
            is_malicious, cat, _ = PathTraversalDetector.inspect(name)
            assert is_malicious is False, f"Clean filename flagged as malicious: {name}"
            assert cat is None


# ==============================================================================
# 2. Storage Vault Confinement Unit Tests
# ==============================================================================

class TestTCSEC003StorageConfinement:
    """Validates StorageVaultManager canonical path resolution and confinement invariants."""

    def test_sanitize_filename_strips_traversal_sequences(self):
        test_cases = [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\win.ini", "win.ini"),
            ("report/../../secret.pdf", "secret.pdf"),
            ("/absolute/path/test.pdf", "test.pdf"),
            ("....//nested//doc.pdf", "doc.pdf"),
        ]
        for untrusted, expected_suffix in test_cases:
            clean = StorageVaultManager.sanitize_filename(untrusted, strict=False)
            assert ".." not in clean
            assert "/" not in clean
            assert "\\" not in clean
            assert clean.endswith(expected_suffix)

    def test_sanitize_filename_strict_mode_rejects_traversal(self):
        with pytest.raises(PathTraversalViolationError):
            StorageVaultManager.sanitize_filename("../../etc/passwd", strict=True)

        with pytest.raises(PathTraversalViolationError):
            StorageVaultManager.sanitize_filename("report.pdf\x00.exe", strict=True)

    def test_get_safe_storage_path_confines_to_vault_root(self):
        virtual_path, physical_path = StorageVaultManager.get_safe_storage_path(
            citizen_id="cit_001",
            doc_id="doc_123",
            filename="../../etc/passwd",
        )
        vault_root = StorageVaultManager.get_vault_root()

        assert physical_path.is_relative_to(vault_root)
        assert ".." not in virtual_path
        assert virtual_path == "/storage/vault/cit_001/doc_123/passwd"
        assert physical_path.name == "passwd"

    def test_resolve_and_confine_rejects_escape_attempts(self):
        escape_attempts = [
            "../../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\cmd.exe",
            "/etc/shadow",
            "C:\\Windows\\win.ini",
            "/var/log/syslog",
        ]
        for attempt in escape_attempts:
            with pytest.raises(PathTraversalViolationError):
                StorageVaultManager.resolve_and_confine(attempt, citizen_id="cit_001")

    def test_resolve_and_confine_blocks_cross_citizen_access(self):
        # Even inside the vault, citizen cit_001 cannot step into cit_002's folder
        with pytest.raises(PathTraversalViolationError):
            StorageVaultManager.resolve_and_confine(
                untrusted_path="../../cit_002/doc_999/secret.pdf",
                citizen_id="cit_001",
            )

    def test_resolve_and_confine_permits_authorized_vault_paths(self):
        vault_root = StorageVaultManager.get_vault_root()
        # Legitimate path inside citizen's folder
        safe_rel = "doc_123/lab_report.pdf"
        resolved = StorageVaultManager.resolve_and_confine(safe_rel, citizen_id="cit_001")
        assert resolved.is_relative_to(vault_root / "cit_001")


# ==============================================================================
# 3. Document Ingestion Traversal Resistance Tests
# ==============================================================================

class TestTCSEC003DocumentIngestion:
    """Validates that document upload endpoints sanitize and confine traversal filenames."""

    def test_ingest_document_with_traversal_filename(self, client: TestClient, clinician_headers: dict):
        fake_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        files = {
            "file": ("../../../../etc/passwd.pdf", io.BytesIO(fake_pdf), "application/pdf")
        }
        data = {
            "citizen_id": "cit_001",
            "title": "Lab Report",
        }

        resp = client.post(
            "/api/v1/documents/ingest",
            data=data,
            files=files,
            headers=clinician_headers,
        )
        assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.text}"
        job_data = resp.json()
        doc_id = job_data["document_id"]

        # Check preserved record in store
        doc_record = store.document_files.get(doc_id)
        assert doc_record is not None
        assert ".." not in doc_record["filename"]
        assert "/" not in doc_record["filename"]
        assert "\\" not in doc_record["filename"]
        assert doc_record["filename"] == "passwd.pdf"

        # Verify physical/storage path is strictly confined within vault
        storage_path = doc_record["storage_path"]
        assert storage_path.startswith("/storage/vault/cit_001/")

    def test_legacy_upload_with_traversal_filename(self, client: TestClient, clinician_headers: dict):
        fake_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        files = {
            "file": ("..\\..\\windows\\system32\\cmd.exe.pdf", io.BytesIO(fake_pdf), "application/pdf")
        }
        data = {
            "citizen_id": "cit_001",
            "title": "Legacy Test Document",
            "document_type": "LAB_REPORT",
        }

        resp = client.post(
            "/api/v1/documents/upload",
            data=data,
            files=files,
            headers=clinician_headers,
        )
        assert resp.status_code == 200
        result = resp.json()
        saved_name = result["document"]["filename"]
        assert ".." not in saved_name
        assert "\\" not in saved_name
        assert "/" not in saved_name


# ==============================================================================
# 4. Document Retrieval Path Confinement Tests
# ==============================================================================

class TestTCSEC003DocumentRetrieval:
    """Validates that document retrieval endpoints reject traversal attempts and confine access."""

    def test_download_by_doc_id_rejects_traversal(self, client: TestClient, clinician_headers: dict):
        # Attempt to download with traversal sequences in doc_id
        resp = client.get(
            "/api/v1/documents/download/..%2f..%2fetc%2fpasswd",
            headers=clinician_headers,
        )
        # Should be rejected with 400 Bad Request
        assert resp.status_code == 400
        data = resp.json()
        assert "path traversal" in data["detail"].lower()

    def test_retrieve_by_path_rejects_system_path_traversal(self, client: TestClient, clinician_headers: dict):
        # Attempt to retrieve /etc/passwd
        resp = client.get(
            "/api/v1/documents/retrieve-by-path?path=../../../../etc/passwd",
            headers=clinician_headers,
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "path traversal" in data["detail"].lower()

    def test_retrieve_by_path_rejects_absolute_windows_path(self, client: TestClient, clinician_headers: dict):
        resp = client.get(
            "/api/v1/documents/retrieve-by-path?path=C:\\Windows\\win.ini",
            headers=clinician_headers,
        )
        assert resp.status_code in (400, 403)

    def test_retrieve_by_path_rejects_cross_citizen_idor(self, client: TestClient, citizen_headers: dict):
        # First seed a document for cit_002
        fake_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        store.document_files["doc_cit002_secret"] = {
            "document_id": "doc_cit002_secret",
            "citizen_id": "cit_002",
            "filename": "confidential_health.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(fake_pdf),
            "sha256_hash": "dummyhash",
            "storage_path": "/storage/vault/cit_002/doc_cit002_secret/confidential_health.pdf",
            "raw_bytes": fake_pdf,
        }

        # cit_001 attempts to access cit_002's file
        resp = client.get(
            "/api/v1/documents/retrieve-by-path?path=/storage/vault/cit_002/doc_cit002_secret/confidential_health.pdf",
            headers=citizen_headers,
        )
        # Blocked with 403 Forbidden
        assert resp.status_code == 403
        assert "unauthorized" in resp.json()["detail"].lower() or "denied" in resp.json()["detail"].lower()

    def test_retrieve_by_path_succeeds_for_authorized_document(self, client: TestClient, clinician_headers: dict):
        # Seed an authorized document
        fake_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        store.document_files["doc_authorized_001"] = {
            "document_id": "doc_authorized_001",
            "citizen_id": "cit_001",
            "filename": "legitimate_report.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(fake_pdf),
            "sha256_hash": "dummyhash",
            "storage_path": "/storage/vault/cit_001/doc_authorized_001/legitimate_report.pdf",
            "raw_bytes": fake_pdf,
        }

        resp = client.get(
            "/api/v1/documents/retrieve-by-path?path=/storage/vault/cit_001/doc_authorized_001/legitimate_report.pdf&citizen_id=cit_001",
            headers=clinician_headers,
        )
        assert resp.status_code == 200
        assert resp.content == fake_pdf
        assert resp.headers["content-type"] == "application/pdf"


# ==============================================================================
# 5. Security Verification Endpoints & Incident Auditing Tests
# ==============================================================================

class TestTCSEC003SecurityEndpoints:
    """Validates the TC-SEC-003 interactive testing endpoints and audit logs."""

    @pytest.mark.parametrize(
        "payload, attack_type",
        [
            ("../../../../etc/passwd", "RELATIVE_TRAVERSAL"),
            ("..\\..\\windows\\system32\\cmd.exe", "WINDOWS_SYSTEM_ROOT"),
            ("%2e%2e%2f%2e%2e%2fetc%2fshadow", "URL_ENCODED"),
            ("report.pdf\x00.exe", "NULL_BYTE"),
            ("../../cit-002/sensitive.pdf", "CROSS_TENANT_ESCAPE"),
        ],
    )
    def test_security_path_traversal_test_endpoint(self, client: TestClient, payload: str, attack_type: str):
        resp = client.post(
            "/api/v1/security/path-traversal-test",
            json={
                "path": payload,
                "attack_type": attack_type,
                "operation": "RETRIEVE",
                "citizen_id": "cit_001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_safe"] is True
        assert data["unauthorized_file_accessed"] is False
        assert data["audit_incident_recorded"] is True
        assert "storage vault" in data["remediation_summary"].lower()

    def test_verify_vault_confinement_endpoint(self, client: TestClient, clinician_headers: dict):
        # 1. Test traversal path
        resp_bad = client.get(
            "/api/v1/documents/vault/verify-confinement?path=../../../../etc/passwd&citizen_id=cit_001",
            headers=clinician_headers,
        )
        assert resp_bad.status_code == 200
        data_bad = resp_bad.json()
        assert data_bad["is_traversal_detected"] is True
        assert data_bad["is_confined_to_vault"] is False
        assert data_bad["confinement_invariant_satisfied"] is False

        # 2. Test safe path
        resp_good = client.get(
            "/api/v1/documents/vault/verify-confinement?path=doc_123/lab_report.pdf&citizen_id=cit_001",
            headers=clinician_headers,
        )
        assert resp_good.status_code == 200
        data_good = resp_good.json()
        assert data_good["is_traversal_detected"] is False
        assert data_good["is_confined_to_vault"] is True
        assert data_good["confinement_invariant_satisfied"] is True

    def test_storage_vault_audit_endpoint(self, client: TestClient):
        resp = client.get("/api/v1/security/storage-vault-audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OPERATIONAL"
        assert data["confinement_enforced"] is True
        assert "vault_root" in data
        assert isinstance(data["total_traversal_attempts_blocked"], int)
