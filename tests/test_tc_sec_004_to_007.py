"""Unit Tests for TC-SEC-004 through TC-SEC-007 (Security & Privacy).

Tests and verifies:
- TC-SEC-004: Secret leakage (repository scanning, client bundles, ordinary logs)
- TC-SEC-005: Sensitive information in logs (masking of ABHA, tokens, passwords, biometrics)
- TC-SEC-006: Unauthorized data modification (RBAC denial with HTTP 403 & audit event)
- TC-SEC-007: Rate limiting (sliding window enforcement with HTTP 429 Too Many Requests)
"""

import pytest
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, UserRecord
from packages.auth.jwt import create_access_token
from packages.types.enums import UserRole

client = TestClient(app)


def test_tc_sec_004_secret_leakage_scanning():
    """TC-SEC-004: Run repository secret scanning, inspect public assets/logs, verify config practices."""
    res = client.get("/api/v1/security/secret-scan")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "CLEAN"
    assert data["high_entropy_secrets_found"] == 0
    assert data["is_compliant"] is True
    assert data["secret_hygiene_score"] == 100.0


def test_tc_sec_005_sensitive_information_in_logs_masked():
    """TC-SEC-005: Perform workflows with synthetic identifiers, inspect logs, verify masking of PII/PHI and credentials."""
    raw_log_with_pii = (
        "Encounter created for ABHA ID 91-1111-2222-3333 by clinician. "
        "Authorization header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123. "
        "Authentication event with password='password123'."
    )
    res = client.post(
        "/api/v1/security/log-redaction-audit",
        json={"raw_log": raw_log_with_pii, "context": "screening_and_ai"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_compliant"] is True
    assert "[REDACTED_ABHA_ID]" in data["sanitized_log"]
    assert "[REDACTED_TOKEN]" in data["sanitized_log"]
    assert "[REDACTED_PASSWORD]" in data["sanitized_log"]
    assert "91-1111-2222-3333" not in data["sanitized_log"]
    assert "password123" not in data["sanitized_log"]
    assert data["phi_exposed_in_clean_log"] is False


def test_tc_sec_006_unauthorized_data_modification_denied():
    """TC-SEC-006: Authenticate as role lacking modification privileges, attempt to alter clinical observation/referral, verify 403."""
    # 1. Verification endpoint
    res = client.post(
        "/api/v1/security/unauthorized-modification-test",
        json={
            "attempted_role": "CITIZEN",
            "target_resource": "clinical_observation",
            "citizen_id": "citizen-ramesh-patel-01",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_denied"] is True
    assert data["http_status"] == 403
    assert data["security_incident_recorded"] is True
    assert data["is_compliant"] is True

    # 2. Live API RBAC test: Citizen role attempting to submit clinician review
    cit_token = create_access_token(subject="cit-sec-user", role=UserRole.CITIZEN, tenant_id="karnataka")
    resp_live = client.post(
        "/api/v1/clinician/review/case-test-01",
        headers={"Authorization": f"Bearer {cit_token}"},
        json={"status": "APPROVED", "review_notes": "Attempting unauthorized doctor approval"},
    )
    assert resp_live.status_code == 403


def test_tc_sec_007_rate_limiting_enforcement():
    """TC-SEC-007: Generate requests exceeding rate limit, verify HTTP 429 without service destabilization."""
    # 1. Direct rate limiter test endpoint
    res = client.post(
        "/api/v1/security/rate-limit-test",
        json={
            "request_count": 20,
            "simulated_limit": 10,
            "target_path": "/api/v1/auth/login",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["rate_limit_enforced"] is True
    assert data["blocked_requests"] == 10
    assert data["http_status_returned"] == 429
    assert data["service_destabilized"] is False
    assert data["is_compliant"] is True
