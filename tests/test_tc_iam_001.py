"""TC-IAM-001: Successful Authentication Test Suite.

Requirement: SYS-IAM-001
Priority: P0
Specification:
- Preconditions: An active synthetic user account exists.
- Steps:
  1. Open the login page.
  2. Submit valid credentials.
  3. Navigate to a protected application page.
  4. Refresh the page.
- Expected Results:
  - Authentication succeeds.
  - The session is established securely.
  - The protected page remains accessible while the session is valid.
  - Session expiration and logout behavior follow configured policies.
"""

import time
from datetime import datetime, timedelta, timezone
import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from packages.types.enums import UserRole
from services.store import store
from packages.auth.jwt import create_access_token

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_iam_environment():
    """Ensure baseline synthetic users and demo data are initialized."""
    seed_all_demo_data()


class TestTCIAM001SuccessfulAuthentication:
    """Rigorous verification of TC-IAM-001 test case against SYS-IAM-001 requirement."""

    # -------------------------------------------------------------------------
    # Preconditions
    # -------------------------------------------------------------------------
    def test_precondition_synthetic_user_accounts_exist(self):
        """Preconditions: Active synthetic user accounts exist for evaluation."""
        synthetic_emails = [
            "doctor@sevahealth.ai",
            "worker@sevahealth.ai",
            "citizen@sevahealth.ai",
            "admin@sevahealth.ai",
        ]
        for email in synthetic_emails:
            user = store.get_user(email)
            assert user is not None, f"Precondition failed: Synthetic user {email} not found"
            assert user.is_active is True, f"Precondition failed: Synthetic user {email} is inactive"
            assert user.hashed_password is not None, f"Precondition failed: Missing credentials for {email}"

    # -------------------------------------------------------------------------
    # Step 1: Open the login page
    # -------------------------------------------------------------------------
    def test_step1_open_login_page_and_entry_points(self):
        """Step 1: Open the login page and portal access entry points."""
        # 1.1 Web Portal Root
        res_portal = client.get("/")
        assert res_portal.status_code == 200
        assert "SevaHealth" in res_portal.text

        # 1.2 Dedicated Health Worker Login portal
        res_hw = client.get("/health-worker-app")
        assert res_hw.status_code == 200
        assert "Health Worker Login" in res_hw.text
        assert "loginEmail" in res_hw.text
        assert "loginPassword" in res_hw.text

        # 1.3 Interactive API documentation for Auth APIs
        res_docs = client.get("/docs")
        assert res_docs.status_code == 200

    # -------------------------------------------------------------------------
    # Step 2: Submit valid credentials
    # -------------------------------------------------------------------------
    def test_step2_submit_valid_credentials_and_secure_session(self):
        """Step 2: Submit valid credentials.

        Expected Result:
        - Authentication succeeds.
        - The session is established securely.
        """
        # Negative validation: Invalid credentials return 401
        res_invalid = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "wrong_password_999"}
        )
        assert res_invalid.status_code == 401
        assert "Incorrect email or password" in res_invalid.json()["detail"]

        # Positive validation: Submit valid credentials
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "password123"}
        )
        assert res_login.status_code == 200, f"Authentication failed: {res_login.text}"
        
        payload = res_login.json()
        assert "access_token" in payload and payload["access_token"]
        assert "refresh_token" in payload and payload["refresh_token"]
        assert payload["token_type"] == "bearer"
        assert payload["role"] == "CLINICIAN"
        assert payload["name"] == "Dr. Anand Kulkarni, MD"
        assert payload["user_id"] == "doctor-user-01"
        assert payload["tenant_id"] == "karnataka_state_health"

        # Security Headers Verification
        headers = res_login.headers
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"

        # Secure Session Record Verification
        sessions = store.list_user_sessions("doctor-user-01")
        assert len(sessions) > 0
        latest_session = sessions[-1]
        assert latest_session.user_id == "doctor-user-01"
        assert latest_session.is_revoked is False
        assert latest_session.expires_at > datetime.now(timezone.utc)

    # -------------------------------------------------------------------------
    # Step 3: Navigate to a protected application page
    # -------------------------------------------------------------------------
    def test_step3_navigate_to_protected_page(self):
        """Step 3: Navigate to a protected application page.

        Expected Result:
        - Protected page is accessible while the session is valid.
        """
        # Obtain fresh valid session
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "password123"}
        )
        token = res_login.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        # 3.1 Protected User Profile Page
        res_profile = client.get("/api/v1/auth/me", headers=auth_headers)
        assert res_profile.status_code == 200
        assert res_profile.json()["email"] == "doctor@sevahealth.ai"
        assert res_profile.json()["role"] == "CLINICIAN"

        # 3.2 Protected Patient Record
        res_patient = client.get("/api/v1/citizens/citizen-ramesh-patel-01", headers=auth_headers)
        assert res_patient.status_code == 200
        assert res_patient.json()["first_name"] == "Ramesh"

        # 3.3 Protected Session Registry
        res_sessions = client.get("/api/v1/auth/sessions", headers=auth_headers)
        assert res_sessions.status_code == 200
        assert len(res_sessions.json()) >= 1

        # 3.4 Protected Clinician Triage Queue
        res_triage = client.get("/api/v1/clinician/triage", headers=auth_headers)
        assert res_triage.status_code == 200

    # -------------------------------------------------------------------------
    # Step 4: Refresh the page
    # -------------------------------------------------------------------------
    def test_step4_refresh_protected_page(self):
        """Step 4: Refresh the page.

        Expected Result:
        - The protected page remains accessible while the session is valid.
        """
        # Login and establish session
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "password123"}
        )
        token = res_login.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        # First visit
        res1 = client.get("/api/v1/auth/me", headers=auth_headers)
        assert res1.status_code == 200

        # Simulate browser refresh (repeat GET with existing session credentials)
        res_refresh_1 = client.get("/api/v1/auth/me", headers=auth_headers)
        assert res_refresh_1.status_code == 200
        assert res_refresh_1.json()["email"] == "doctor@sevahealth.ai"

        # Refresh clinical page
        res_refresh_2 = client.get("/api/v1/citizens/citizen-ramesh-patel-01", headers=auth_headers)
        assert res_refresh_2.status_code == 200
        assert res_refresh_2.json()["first_name"] == "Ramesh"

        # Refresh sessions page
        res_refresh_3 = client.get("/api/v1/auth/sessions", headers=auth_headers)
        assert res_refresh_3.status_code == 200

    # -------------------------------------------------------------------------
    # Expected Result: Session Expiration & Logout Policies
    # -------------------------------------------------------------------------
    def test_session_expiration_and_logout_policies(self):
        """Expected Result: Session expiration and logout behavior follow configured policies."""
        # 1. Establish session
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "password123"}
        )
        access_token = res_login.json()["access_token"]
        refresh_token = res_login.json()["refresh_token"]

        # 2. Token Refresh Policy: /api/v1/auth/refresh rotates token pair
        res_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert res_refresh.status_code == 200
        new_access_token = res_refresh.json()["access_token"]
        new_refresh_token = res_refresh.json()["refresh_token"]
        assert new_access_token != access_token

        # 3. Token Rotation / Replay Prevention Policy: Old refresh token is revoked
        res_replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert res_replay.status_code == 401
        assert "revoked" in res_replay.json()["detail"].lower()

        # 4. New rotated token provides access
        res_new_access = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
        assert res_new_access.status_code == 200

        # 5. Logout Policy: /api/v1/auth/logout revokes session & access token
        res_logout = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {new_access_token}"})
        assert res_logout.status_code == 200
        assert res_logout.json()["status"] == "success"

        # 6. Post-Logout Policy: Revoked token cannot access protected pages
        res_denied = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
        assert res_denied.status_code == 401
        assert "revoked" in res_denied.json()["detail"].lower()

        # 7. Expired Token Policy: Expired JWT is rejected
        expired_token = create_access_token(
            subject="doctor-user-01",
            tenant_id="karnataka_state_health",
            role=UserRole.CLINICIAN,
            expires_delta=timedelta(seconds=-30),  # expired 30 seconds ago
        )
        res_expired = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        assert res_expired.status_code == 401
        assert "Could not validate credentials" in res_expired.json()["detail"]

    # -------------------------------------------------------------------------
    # Multi-Role Synthetic User Matrix Verification
    # -------------------------------------------------------------------------
    @pytest.mark.parametrize("email,expected_role", [
        ("citizen@sevahealth.ai", UserRole.CITIZEN),
        ("worker@sevahealth.ai", UserRole.HEALTH_WORKER),
        ("doctor@sevahealth.ai", UserRole.CLINICIAN),
        ("admin@sevahealth.ai", UserRole.PUBLIC_HEALTH_ADMIN),
    ])
    def test_multi_persona_synthetic_authentication(self, email, expected_role):
        """Verifies TC-IAM-001 succeeds across all standard synthetic roles."""
        res = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
        assert res.status_code == 200
        token = res.json()["access_token"]
        assert res.json()["role"] == expected_role.value

        # Navigate to protected page
        res_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res_me.status_code == 200
        assert res_me.json()["email"] == email

        # Refresh page
        res_ref = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res_ref.status_code == 200
