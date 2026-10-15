"""TC-IAM-002: Invalid Credentials Test Suite.

Requirement: SYS-IAM-002
Priority: P0
Specification:
- Steps:
  1. Submit an invalid password.
  2. Attempt to access a protected API.
- Expected Results:
  - Authentication fails (HTTP 401).
  - No protected information is returned.
  - The error message does not reveal whether a sensitive account exists (uniform error & timing defense).
  - Security monitoring records relevant repeated failures.
"""

from datetime import datetime, timedelta, timezone
import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from packages.types.enums import UserRole, AuditAction
from services.store import store
from packages.auth.jwt import create_access_token
from packages.observability.audit import audit_logger

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_iam_environment():
    """Ensure synthetic users, clinical demo data, and store baseline are initialized."""
    seed_all_demo_data()
    store.clear_security_incidents()


class TestTCIAM002InvalidCredentials:
    """Rigorous verification of TC-IAM-002 test case against SYS-IAM-002 requirement."""

    # -------------------------------------------------------------------------
    # Preconditions
    # -------------------------------------------------------------------------
    def test_precondition_sensitive_accounts_exist(self):
        """Precondition: Sensitive accounts across healthcare roles exist."""
        sensitive_roles = [
            ("doctor@sevahealth.ai", UserRole.CLINICIAN),
            ("worker@sevahealth.ai", UserRole.HEALTH_WORKER),
            ("citizen@sevahealth.ai", UserRole.CITIZEN),
            ("admin@sevahealth.ai", UserRole.PUBLIC_HEALTH_ADMIN),
        ]
        for email, role in sensitive_roles:
            user = store.get_user(email)
            assert user is not None, f"Sensitive account {email} missing"
            assert user.role == role

    # -------------------------------------------------------------------------
    # Step 1: Submit an invalid password
    # -------------------------------------------------------------------------
    @pytest.mark.parametrize("email", [
        "doctor@sevahealth.ai",
        "worker@sevahealth.ai",
        "citizen@sevahealth.ai",
        "admin@sevahealth.ai",
    ])
    def test_step1_submit_invalid_password_fails_with_401(self, email):
        """Step 1: Submit an invalid password.

        Expected Result:
        - Authentication fails with HTTP 401 Unauthorized.
        - No protected information is returned.
        - Uniform error message: 'Incorrect email or password'.
        """
        res = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "completely_wrong_password_999!"}
        )

        # 1.1 Authentication must fail with HTTP 401
        assert res.status_code == 401, f"Expected 401 for {email}, got {res.status_code}"

        # 1.2 No protected information returned (no tokens, no session id, no user metadata)
        payload = res.json()
        assert "access_token" not in payload
        assert "refresh_token" not in payload
        assert "user_id" not in payload
        assert "role" not in payload
        assert "tenant_id" not in payload

        # 1.3 Uniform error message
        assert payload.get("detail") == "Incorrect email or password"

        # 1.4 Security Headers enforced on rejection
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("x-frame-options") == "DENY"

    # -------------------------------------------------------------------------
    # Requirement: Error message does NOT reveal whether account exists
    # -------------------------------------------------------------------------
    def test_error_message_does_not_reveal_account_existence(self):
        """Expected Result: The error message does not reveal whether a sensitive account exists.

        Both existing and non-existent accounts must return identical HTTP 401 status
        and identical error payloads, preventing user enumeration attacks.
        """
        # Case A: Existing sensitive clinician account with invalid password
        res_existing = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor@sevahealth.ai", "password": "invalid_doctor_pass"}
        )

        # Case B: Non-existent synthetic user account
        res_non_existent = client.post(
            "/api/v1/auth/login",
            json={"email": "unknown_vip_physician@sevahealth.ai", "password": "arbitrary_password_123"}
        )

        # Both must return HTTP 401
        assert res_existing.status_code == 401
        assert res_non_existent.status_code == 401

        # Both must return the identical detail message without leaking account existence
        assert res_existing.json() == res_non_existent.json()
        assert res_existing.json()["detail"] == "Incorrect email or password"
        assert "user not found" not in res_non_existent.text.lower()
        assert "does not exist" not in res_non_existent.text.lower()

    # -------------------------------------------------------------------------
    # Step 2: Attempt to access a protected API
    # -------------------------------------------------------------------------
    def test_step2_attempt_access_protected_api_with_invalid_token(self):
        """Step 2: Attempt to access a protected API with invalid / forged token.

        Expected Result:
        - Authentication fails.
        - No protected information is returned (0 PHI / 0 clinical data).
        """
        protected_endpoints = [
            "/api/v1/auth/me",
            "/api/v1/auth/sessions",
            "/api/v1/citizens/citizen-ramesh-patel-01",
            "/api/v1/clinician/triage",
        ]

        invalid_auth_headers = [
            {"Authorization": "Bearer forged_invalid_jwt_signature"},
            {"Authorization": "Bearer malformed-token-string"},
            {"Authorization": "Bearer 12345"},
        ]

        for endpoint in protected_endpoints:
            for headers in invalid_auth_headers:
                res = client.get(endpoint, headers=headers)
                assert res.status_code == 401, f"Endpoint {endpoint} allowed access with invalid token: {res.text}"

                # Verify no protected information is returned
                body = res.text
                assert "Ramesh" not in body
                assert "Lakshmi" not in body
                assert "blood_glucose" not in body
                assert "systolic_bp" not in body
                assert "doctor-user-01" not in body

    def test_step2_attempt_access_protected_api_with_expired_token(self):
        """Step 2: Expired tokens are rejected with 401 and return no protected info."""
        expired_token = create_access_token(
            subject="doctor-user-01",
            tenant_id="karnataka_state_health",
            role=UserRole.CLINICIAN,
            expires_delta=timedelta(seconds=-60),
        )

        res = client.get(
            "/api/v1/citizens/citizen-ramesh-patel-01",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert res.status_code == 401
        assert "Ramesh" not in res.text

    def test_step2_attempt_access_protected_api_with_revoked_token(self):
        """Step 2: Revoked / logged-out tokens are rejected with 401."""
        # 1. Login to get token
        login_res = client.post("/api/v1/auth/login", json={"email": "doctor@sevahealth.ai", "password": "password123"})
        token = login_res.json()["access_token"]

        # 2. Logout to revoke
        client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

        # 3. Subsequent call must fail
        res = client.get(
            "/api/v1/citizens/citizen-ramesh-patel-01",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 401
        assert "revoked" in res.json()["detail"].lower()

    # -------------------------------------------------------------------------
    # Expected Result: Security monitoring records relevant repeated failures
    # -------------------------------------------------------------------------
    def test_security_monitoring_records_repeated_failures(self):
        """Expected Result: Security monitoring records relevant repeated failures.

        Simulates repeated failed password submissions (brute-force scenario),
        verifies that consecutive failures are tracked in the store and a
        security incident alert is recorded by security monitoring & audit logging.
        """
        store.clear_security_incidents()
        monitored_email = "target_doctor@sevahealth.ai"

        # 1. Submit 3 consecutive invalid authentication attempts
        for attempt in range(1, 4):
            res = client.post(
                "/api/v1/auth/login",
                json={"email": monitored_email, "password": f"bad_password_attempt_{attempt}"}
            )
            assert res.status_code == 401

        # 2. Verify store records repeated failure count
        tracker = store.failed_login_tracker.get(monitored_email, [])
        assert len(tracker) == 3, f"Expected 3 tracked failures, got {len(tracker)}"

        # 3. Verify security incident alert was generated
        incidents = store.get_security_incidents()
        assert len(incidents) >= 1, "Security incident was not recorded"
        latest = incidents[-1]
        assert latest["identifier"] == monitored_email
        assert latest["consecutive_failures"] >= 3
        assert latest["threat_type"] == "REPEATED_AUTHENTICATION_FAILURES"
        assert latest["status"] == "ALERT_ACTIVE"

        # 4. Verify audit logger captured both ACCESS_DENIED and SECURITY_ALERT
        audit_trail = audit_logger.list_records(limit=10)
        denied_events = [r for r in audit_trail if r.action == AuditAction.ACCESS_DENIED]
        alert_events = [r for r in audit_trail if r.action == AuditAction.SECURITY_ALERT]

        assert len(denied_events) >= 3, "Audit trail missing ACCESS_DENIED records"
        assert len(alert_events) >= 1, "Audit trail missing SECURITY_ALERT record"
        assert alert_events[-1].details.get("threat_type") == "REPEATED_AUTHENTICATION_FAILURES"

        # 5. Verify security monitoring API endpoint exposes recorded incidents
        mon_res = client.get("/api/v1/auth/security-monitoring")
        assert mon_res.status_code == 200
        mon_data = mon_res.json()
        assert mon_data["status"] == "OPERATIONAL"
        assert mon_data["monitoring_active"] is True
        assert mon_data["repeated_failure_threshold"] == 3
        assert mon_data["active_incidents_count"] >= 1
        assert any(inc["identifier"] == monitored_email for inc in mon_data["recent_incidents"])

    # -------------------------------------------------------------------------
    # Account Lockout & Counter Reset Behavior
    # -------------------------------------------------------------------------
    def test_account_lockout_and_counter_reset_policy(self):
        """Verifies account lockout occurs on 5 failures and reset occurs on success."""
        email = "doctor@sevahealth.ai"
        user = store.get_user(email)
        assert user is not None

        # Reset counters
        store.record_successful_login(email)
        assert user.failed_login_attempts == 0
        assert user.is_locked is False

        # Submit 5 invalid attempts to trigger lockout
        for _ in range(5):
            client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})

        assert user.failed_login_attempts >= 5
        assert user.is_locked is True

        # Next attempt is rejected even if correct password (due to active lockout)
        locked_res = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
        assert locked_res.status_code == 401
        assert locked_res.json()["detail"] == "Incorrect email or password"  # Uniform error

        # Reset account for ongoing testing
        store.record_successful_login(email)
        assert user.is_locked is False
        assert user.failed_login_attempts == 0

        # Legitimate login succeeds again
        good_res = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
        assert good_res.status_code == 200
        assert "access_token" in good_res.json()
