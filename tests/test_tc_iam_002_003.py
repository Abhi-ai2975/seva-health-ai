"""Automated Test Suite for TC-IAM-002 and TC-IAM-003.

TC-IAM-002: Invalid credentials
Priority: P0
Steps:
1. Submit an invalid password.
2. Attempt to access a protected API.
Expected result:
- Authentication fails.
- No protected information is returned.
- The error message does not reveal whether a sensitive account exists.
- Security monitoring records relevant repeated failures.

TC-IAM-003: Citizen cross-patient access
Priority: P0
Preconditions: Two synthetic citizens, A and B, exist.
Steps:
1. Authenticate as Citizen A.
2. Request Citizen B's profile.
3. Attempt to retrieve Citizen B's observations and risk assessments.
4. Attempt to modify Citizen B's profile.
Expected result:
- All unauthorized operations are denied.
- No protected information is returned.
- Attempts are logged appropriately.
"""

import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from packages.types.enums import AuditAction, UserRole
from packages.observability.audit import audit_logger
from services.store import store

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_iam_environment():
    """Ensure baseline synthetic users and demo data are initialized."""
    seed_all_demo_data()


# =============================================================================
# TC-IAM-002: Invalid Credentials
# =============================================================================
class TestTCIAM002InvalidCredentials:
    """Rigorous verification of TC-IAM-002: Invalid credentials."""

    def test_invalid_password_fails_authentication(self):
        """Submit an invalid password -> Authentication fails with 401."""
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "WrongPassword2026!"}
        )
        assert res.status_code == 401
        data = res.json()
        assert "access_token" not in data
        assert "refresh_token" not in data
        assert "token_type" not in data
        assert "Incorrect email or password" in data["detail"]

    def test_anti_enumeration_error_message_uniformity(self):
        """Error message does not reveal whether a sensitive account exists."""
        # 1. Existing sensitive account with invalid password
        res_existing = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@sevahealth.ai", "password": "BadPassword123!"}
        )
        # 2. Non-existent account
        res_nonexistent = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost.investigator.999@sevahealth.ai", "password": "AnyPassword123!"}
        )

        assert res_existing.status_code == 401
        assert res_nonexistent.status_code == 401
        # Details must match exactly to prevent account enumeration / user discovery
        assert res_existing.json()["detail"] == res_nonexistent.json()["detail"]
        assert res_existing.json()["detail"] == "Incorrect email or password"

    def test_unauthenticated_protected_api_access_blocked(self):
        """Attempt to access protected APIs without valid authentication."""
        endpoints = [
            "/api/v1/auth/me",
            "/api/v1/citizens/citizen-ramesh-patel-01",
            "/api/v1/clinician/triage",
            "/api/v1/trajectory/citizen-ramesh-patel-01",
        ]
        for ep in endpoints:
            res = client.get(ep)
            assert res.status_code == 401, f"Expected 401 for unauthenticated {ep}, got {res.status_code}"
            # Ensure no patient or user data is leaked
            assert "first_name" not in res.text
            assert "access_token" not in res.text

    def test_security_monitoring_records_repeated_failures(self):
        """Security monitoring records relevant repeated failures in audit trail."""
        test_email = "doctor@sevahealth.ai"
        initial_records_count = len(audit_logger.list_records(limit=500))

        # Perform 3 consecutive bad login attempts
        for i in range(3):
            client.post("/api/v1/auth/login", json={"email": test_email, "password": f"BadPass_{i}"})

        recent_audits = audit_logger.list_records(limit=20)
        denied_logins = [
            r for r in recent_audits
            if r.action == AuditAction.ACCESS_DENIED and r.actor_id == test_email
        ]
        assert len(denied_logins) >= 3, "Expected repeated failed logins to be recorded in audit log"
        for record in denied_logins:
            assert record.resource_type == "User"
            assert record.details.get("reason") == "Invalid credentials"


# =============================================================================
# TC-IAM-003: Citizen Cross-Patient Access
# =============================================================================
class TestTCIAM003CitizenCrossPatientAccess:
    """Rigorous verification of TC-IAM-003: Citizen cross-patient access boundary."""

    CITIZEN_A_EMAIL = "citizen@sevahealth.ai"
    CITIZEN_A_ID = "citizen-ramesh-patel-01"
    CITIZEN_B_ID = "citizen-lakshmi-devi-02"

    @classmethod
    def get_citizen_a_token(cls) -> str:
        res = client.post("/api/v1/auth/login", json={"email": cls.CITIZEN_A_EMAIL, "password": "password123"})
        assert res.status_code == 200
        return res.json()["access_token"]

    def test_preconditions_synthetic_citizens_exist(self):
        """Preconditions: Two synthetic citizens, A and B, exist."""
        cit_a = store.get_citizen(self.CITIZEN_A_ID)
        cit_b = store.get_citizen(self.CITIZEN_B_ID)
        assert cit_a is not None, f"Citizen A ({self.CITIZEN_A_ID}) not found"
        assert cit_b is not None, f"Citizen B ({self.CITIZEN_B_ID}) not found"
        assert cit_a.id != cit_b.id

    def test_citizen_a_can_access_own_profile(self):
        """Citizen A can legitimately access their own record."""
        token_a = self.get_citizen_a_token()
        res = client.get(f"/api/v1/citizens/{self.CITIZEN_A_ID}", headers={"Authorization": f"Bearer {token_a}"})
        assert res.status_code == 200
        assert res.json()["first_name"] == "Ramesh"

    def test_citizen_a_cannot_access_citizen_b_profile(self):
        """Request Citizen B's profile -> Denied with 403, no PHI returned."""
        token_a = self.get_citizen_a_token()
        headers = {"Authorization": f"Bearer {token_a}"}

        # 1. REST Profile
        res_rest = client.get(f"/api/v1/citizens/{self.CITIZEN_B_ID}", headers=headers)
        assert res_rest.status_code == 403
        assert "Citizens can only access their own medical records" in res_rest.json()["detail"]
        assert "Lakshmi" not in res_rest.text

        # 2. FHIR Patient Profile
        res_fhir = client.get(f"/api/v1/fhir/Patient/{self.CITIZEN_B_ID}", headers=headers)
        assert res_fhir.status_code == 403
        assert "Lakshmi" not in res_fhir.text

    def test_citizen_a_cannot_retrieve_citizen_b_observations_and_risks(self):
        """Attempt to retrieve Citizen B's observations and risk assessments -> All denied with 403."""
        token_a = self.get_citizen_a_token()
        headers = {"Authorization": f"Bearer {token_a}"}

        # 1. REST observations
        res_obs = client.get(f"/api/v1/citizens/{self.CITIZEN_B_ID}/observations", headers=headers)
        assert res_obs.status_code == 403

        # 2. FHIR observations
        res_fhir_obs = client.get(f"/api/v1/fhir/Observation?citizen_id={self.CITIZEN_B_ID}", headers=headers)
        assert res_fhir_obs.status_code == 403

        # 3. REST risk
        res_risk = client.get(f"/api/v1/citizens/{self.CITIZEN_B_ID}/risk", headers=headers)
        assert res_risk.status_code == 403

        # 4. Risk engine direct query
        res_engine = client.get(f"/api/v1/risk/latest/{self.CITIZEN_B_ID}", headers=headers)
        assert res_engine.status_code == 403

        # 5. Trajectory engine query
        res_traj = client.get(f"/api/v1/trajectory/{self.CITIZEN_B_ID}", headers=headers)
        assert res_traj.status_code == 403

        # 6. FHIR risk assessment
        res_fhir_risk = client.get(f"/api/v1/fhir/RiskAssessment/{self.CITIZEN_B_ID}", headers=headers)
        assert res_fhir_risk.status_code == 403

        # 7. Screening history
        res_screen = client.get(f"/api/v1/screening/history/{self.CITIZEN_B_ID}", headers=headers)
        assert res_screen.status_code == 403

    def test_citizen_a_cannot_modify_citizen_b_profile(self):
        """Attempt to modify Citizen B's profile -> Denied with 403, record untouched."""
        token_a = self.get_citizen_a_token()
        headers = {"Authorization": f"Bearer {token_a}"}

        # 1. PUT attempt
        put_payload = {
            "first_name": "IllicitName",
            "phone": "+91 99999 99999",
            "village_or_ward": "Compromised Ward"
        }
        res_put = client.put(f"/api/v1/citizens/{self.CITIZEN_B_ID}", json=put_payload, headers=headers)
        assert res_put.status_code == 403
        assert "Citizens can only access their own medical records" in res_put.json()["detail"]

        # 2. PATCH attempt
        res_patch = client.patch(f"/api/v1/citizens/{self.CITIZEN_B_ID}", json={"phone": "+91 00000 00000"}, headers=headers)
        assert res_patch.status_code == 403
        assert "Citizens can only access their own medical records" in res_patch.json()["detail"]

        # 3. Verify Citizen B's profile remains unchanged
        cit_b = store.get_citizen(self.CITIZEN_B_ID)
        assert cit_b.first_name == "Lakshmi"
        assert cit_b.first_name != "IllicitName"

    def test_unauthorized_attempts_are_audited(self):
        """Attempts are logged appropriately in the audit log."""
        token_a = self.get_citizen_a_token()
        headers = {"Authorization": f"Bearer {token_a}"}

        # Trigger unauthorized read
        client.get(f"/api/v1/citizens/{self.CITIZEN_B_ID}", headers=headers)

        recent_audits = audit_logger.list_records(limit=20)
        cross_access_logs = [
            r for r in recent_audits
            if r.action == AuditAction.ACCESS_DENIED and r.resource_id == self.CITIZEN_B_ID
        ]
        assert len(cross_access_logs) >= 1
        record = cross_access_logs[-1]
        assert record.actor_role == UserRole.CITIZEN
        assert "Citizens can only access their own medical records" in record.details.get("reason", "")
