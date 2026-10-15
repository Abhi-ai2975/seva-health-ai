"""Test Suite for TC-SEC-001: SQL Injection Defense and Query Parameterization.

Specification:
15. Test Cases — Security and Privacy
TC-SEC-001: SQL injection
Priority: P0
Steps:
1. Submit representative SQL-injection payloads to relevant inputs.
2. Inspect API behavior and database effects.
Expected result: Inputs are handled safely, database queries remain properly parameterized,
and unauthorized data is not exposed.
"""

import pytest
from starlette.testclient import TestClient
from datetime import datetime, timezone

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord
from packages.types.enums import UserRole, Gender
from packages.auth.jwt import create_access_token
from packages.security.sql_guard import (
    SQLInjectionDetector,
    SQLInjectionViolationError,
    SafeQueryExecutor,
)
from packages.interop.security import AntiArbitraryQueryGuard, ArbitraryQueryViolationError
from packages.interop.adapters.emr_gql_adapter import EmrGqlAdapter
from packages.interop.base import AccessContext

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_security_database():
    """Ensure baseline database state for SQL injection testing."""
    # Seed known test citizen and clinician if needed
    test_cit = CitizenRecord(
        id="cit-sec-001",
        tenant_id="karnataka_state_health",
        user_id="user-sec-citizen",
        abha_id="91-0000-1111-2222",
        first_name="Ramesh",
        last_name="SecurityTest",
        birth_date="1980-01-01",
        gender=Gender.MALE,
        phone="+919876543210",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru South",
        village_or_ward="Ward 1",
    )
    store.add_citizen(test_cit)
    yield
    # Teardown
    if "cit-sec-001" in store.citizens:
        del store.citizens["cit-sec-001"]


# ==============================================================================
# 1. Authentication Endpoint Injection Tests (Auth Bypass Prevention)
# ==============================================================================

class TestTCSEC001AuthBypass:
    """Verifies that authentication inputs handle SQL injection safely without bypass."""

    @pytest.mark.parametrize("malicious_email", [
        "' OR '1'='1",
        "' OR 1=1 --",
        "admin'--",
        "admin' /*",
        "' OR ''='",
        "admin' or '1'='1' --",
        "admin'; DROP TABLE users; --",
    ])
    def test_auth_login_rejects_sql_injection_payloads(self, malicious_email):
        """Tautology payloads in email/username must NOT grant authentication or bypass credential check."""
        initial_user_count = len(store.users)

        resp = client.post(
            "/api/v1/auth/token",
            json={"email": malicious_email, "password": "AnyPassword123!"},
        )

        # Must reject with 401 Unauthorized (or 400), NEVER 200 OK
        assert resp.status_code in (401, 400), f"Expected 401/400 for {malicious_email}, got {resp.status_code}"
        
        # Verify no token or user data leaked
        body = resp.json()
        assert "access_token" not in body
        assert "user_id" not in body

        # Verify database was not tampered with
        assert len(store.users) == initial_user_count
        assert store.verify_database_integrity()["tables_intact"] is True


# ==============================================================================
# 2. Resource Lookup & Search Injection Tests (Data Exfiltration Prevention)
# ==============================================================================

class TestTCSEC001ResourceLookups:
    """Verifies that resource lookups safely bind input parameters without exposing unauthorized data."""

    @pytest.fixture
    def clinician_token(self):
        return create_access_token(
            subject="clinician-sec-01",
            tenant_id="karnataka_state_health",
            role=UserRole.CLINICIAN,
            scopes=["*"],
        )

    @pytest.mark.parametrize("malicious_id", [
        "cit-sec-001' UNION SELECT * FROM users --",
        "cit-sec-001' UNION SELECT id, email, hashed_password FROM users --",
        "' OR '1'='1",
        "cit-sec-001'; DROP TABLE citizens; --",
        "cit-sec-001' /* comment */",
    ])
    def test_citizen_lookup_with_sql_injection_id(self, clinician_token, malicious_id):
        """Malicious ID parameter must be treated as literal string; never dumps table or leaks unauthorized data."""
        headers = {"Authorization": f"Bearer {clinician_token}"}
        initial_citizen_count = len(store.citizens)

        resp = client.get(f"/api/v1/citizens/{malicious_id}", headers=headers)

        # Must return 404 (not found) or 400 (security error), NEVER 200 with dumped data
        assert resp.status_code in (404, 400)

        # If body is JSON, confirm it did not leak password hashes or entire citizen list
        body = resp.json()
        assert "hashed_password" not in str(body)
        assert len(store.citizens) == initial_citizen_count
        assert store.verify_database_integrity()["tables_intact"] is True


# ==============================================================================
# 3. Clinical Data Submission Tests (Stacked DDL & Destruction Prevention)
# ==============================================================================

class TestTCSEC001DataSubmissionDestruction:
    """Verifies that text inputs containing destructive SQL statements are stored safely as text literals."""

    @pytest.fixture
    def health_worker_token(self):
        return create_access_token(
            subject="hw-sec-01",
            tenant_id="karnataka_state_health",
            role=UserRole.HEALTH_WORKER,
            scopes=["*"],
        )

    def test_screening_notes_with_destructive_stacked_ddl(self, health_worker_token):
        """Stacked query '; DROP TABLE citizens; --' submitted in clinical notes must NOT execute."""
        headers = {"Authorization": f"Bearer {health_worker_token}"}
        initial_integrity = store.verify_database_integrity()
        pre_counts = dict(initial_integrity["table_counts"])

        destructive_payload = "Patient reports mild headache'; DROP TABLE citizens; DROP TABLE users; --"

        # Submit screening session with malicious notes
        resp = client.post(
            "/api/v1/screening/",
            headers=headers,
            json={
                "citizen_id": "cit-sec-001",
                "notes": destructive_payload,
                "idrs": {
                    "age_category": "<35",
                    "waist_category": "<90",
                    "physical_activity": "Vigorous",
                    "family_history": "None",
                },
            },
        )

        assert resp.status_code in (200, 201)

        # Verify database was NOT destroyed: tables and counts intact
        post_integrity = store.verify_database_integrity()
        assert post_integrity["tables_intact"] is True
        assert post_integrity["table_counts"]["citizens"] == pre_counts["citizens"]
        assert post_integrity["table_counts"]["users"] == pre_counts["users"]
        assert "cit-sec-001" in store.citizens


# ==============================================================================
# 4. Clinical Interoperability Security Guard Tests
# ==============================================================================

class TestTCSEC001ClinicalInteropGuard:
    """Verifies that AntiArbitraryQueryGuard blocks arbitrary SQL and query injections."""

    @pytest.mark.parametrize("sql_vector", [
        "SELECT * FROM citizens WHERE id = 'cit_001'",
        "' UNION SELECT password FROM users --",
        "DROP TABLE patients;",
        "INSERT INTO users VALUES ('hacker', 'admin');",
        "cit_001' OR 1=1 --",
    ])
    def test_interop_guard_blocks_sql_injection(self, sql_vector):
        """Guard intercepts and raises ArbitraryQueryViolationError on SQL patterns."""
        with pytest.raises(ArbitraryQueryViolationError):
            AntiArbitraryQueryGuard.inspect_query_parameter("patient_id", sql_vector)

    @pytest.mark.asyncio
    async def test_emr_adapter_rejects_sql_injection_attempt(self):
        """EMR GraphQL adapter rejects malicious patient IDs with ArbitraryQueryViolationError."""
        adapter = EmrGqlAdapter()
        context = AccessContext(actor_id="doc_1", actor_role=UserRole.CLINICIAN, purpose="CARE")
        malicious_id = "cit_001' UNION SELECT * FROM users --"

        with pytest.raises(ArbitraryQueryViolationError):
            await adapter.get_observations(malicious_id, context)


# ==============================================================================
# 5. Query Parameterization Invariant (Literal Binding vs Raw Concatenation)
# ==============================================================================

class TestTCSEC001ParameterizationInvariant:
    """Demonstrates and mathematically proves that bound parameterization neutralizes SQL injection."""

    def test_parameter_binding_neutralizes_tautology(self):
        """Tautology payload (' OR '1'='1) evaluates to 0 matches under parameter binding."""
        # Setup sample dataset
        records = [
            {"id": "cit_001", "name": "Alice", "district": "Mysuru"},
            {"id": "cit_002", "name": "Bob", "district": "Mandya"},
        ]

        tautology_payload = "' OR '1'='1"

        # 1. Unparameterized concatenation representation (vulnerable)
        vuln_sql, label = SafeQueryExecutor.simulate_vulnerable_query(tautology_payload)
        assert "WHERE id = '' OR '1'='1'" in vuln_sql
        assert label == "VULNERABLE_RAW_CONCATENATION"

        # 2. Parameterized execution representation (secure)
        # Binds the tautology strictly as a literal search string
        safe_matches = SafeQueryExecutor.execute_in_memory_parameterized(
            records=records,
            filter_field="id",
            bound_param_value=tautology_payload,
        )

        # Proves 0 records exposed (tautology was treated as literal value, not executable SQL syntax)
        assert len(safe_matches) == 0

    def test_parameter_binding_neutralizes_union_select(self):
        """UNION SELECT payload matches 0 records under parameter binding."""
        union_payload = "cit_001' UNION SELECT id, user_id FROM citizens --"
        results = store.execute_parameterized_query("citizens", {"id": union_payload})
        assert len(results) == 0


# ==============================================================================
# 6. Security Router Verification Endpoints & Database Integrity Audit
# ==============================================================================

class TestTCSEC001SecurityEndpoints:
    """Verifies the dedicated security inspection endpoints for automated auditing and frontend testing."""

    @pytest.mark.parametrize("payload, attack_type", [
        ("' OR '1'='1", "TAUTOLOGY"),
        ("cit_001' UNION SELECT * FROM users --", "UNION_SELECT"),
        ("'; DROP TABLE citizens; --", "STACKED_DROP"),
        ("1' AND (SELECT SLEEP(5)) --", "BLIND_TIME"),
        ("cit_001' /* comment */", "COMMENT_TRUNCATION"),
    ])
    def test_security_sql_injection_test_endpoint(self, payload, attack_type):
        """POST /api/v1/security/sql-injection-test verifies input safety and zero data exposure."""
        resp = client.post(
            "/api/v1/security/sql-injection-test",
            json={
                "payload": payload,
                "attack_type": attack_type,
                "target_parameter": "id",
                "target_collection": "citizens",
            },
        )

        assert resp.status_code == 200
        data = resp.json()

        assert data["is_safe"] is True
        assert data["injection_detected"] is True
        assert data["unauthorized_data_exposed"] is False
        assert data["database_modified"] is False
        assert data["database_integrity"]["tables_intact"] is True
        assert data["audit_incident_recorded"] is True

    def test_database_integrity_endpoint(self):
        """GET /api/v1/security/database-integrity returns healthy schema status."""
        resp = client.get("/api/v1/security/database-integrity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SECURE_AND_INTACT"
        assert data["tables_intact"] is True
        assert data["parameterization_enforced"] is True
        assert data["unauthorized_data_exposed"] is False
        assert data["table_counts"]["citizens"] >= 1
